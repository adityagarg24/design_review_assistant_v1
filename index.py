#!/usr/bin/env python3

import json
import os
import re
from datetime import datetime
from pathlib import Path

def load_json(file_path):
    """Load and parse JSON file"""
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading {file_path}: {e}")
        return None

def load_jsx(file_path):
    """Load JSX file content"""
    try:
        with open(file_path, 'r') as f:
            return f.read()
    except Exception as e:
        print(f"Error loading {file_path}: {e}")
        return None

def parse_jsx_props(jsx_content):
    """Extract props from JSX content dynamically"""
    props = {}
    
    # Parse standard JSX props: prop="value"
    prop_pattern = r'(\w+)="([^"]+)"'
    matches = re.findall(prop_pattern, jsx_content)
    
    for key, value in matches:
        if key == 'style':
            continue
        props[key] = parse_property_value(value)
    
    # Parse CSS style object dynamically: {prop: "value"}
    style_object_pattern = r'style=\{\{([^}]+)\}\}'
    style_match = re.search(style_object_pattern, jsx_content)
    
    if style_match:
        style_content = style_match.group(1)
        # Extract individual CSS properties
        css_prop_pattern = r'(\w+):\s*([^,}]+)'
        css_matches = re.findall(css_prop_pattern, style_content)
        
        for css_key, css_value in css_matches:
            # Clean up the value (remove quotes, whitespace)
            clean_value = css_value.strip().strip('"\'')
            
            # Map CSS properties to common names
            prop_name = map_css_property_name(css_key)
            props[prop_name] = parse_property_value(clean_value)
    
    return props

def parse_property_value(value):
    """Parse a property value and extract relevant metadata"""
    # Handle CSS variables: var(--color-token-name)
    css_var_pattern = r'var\(--(?:color-)?([^)]+)\)'
    css_var_match = re.search(css_var_pattern, value)
    if css_var_match:
        token_name = css_var_match.group(1)
        return {'token': token_name, 'value': token_name}
    
    # Handle pixel values
    if isinstance(value, str) and value.endswith('px'):
        if ' ' in value:
            # Compound value like "8px 12px"
            return {'value': value}
        else:
            # Single pixel value
            try:
                return {'value': value, 'normalized': int(value.replace('px', ''))}
            except ValueError:
                return {'value': value}
    
    # Handle percentage values
    if isinstance(value, str) and value.endswith('%'):
        return {'value': value, 'type': 'percentage'}
    
    # Handle numeric values (like fontWeight)
    if isinstance(value, str) and value.isdigit():
        return {'value': value, 'normalized': int(value)}
    
    # Handle token-like values (contain hyphens)
    if isinstance(value, str) and '-' in value and not value.startswith('var('):
        return {'token': value, 'value': value}
    
    # Default case
    return {'value': value}

def map_css_property_name(css_prop):
    """Map CSS property names to consistent component prop names"""
    mapping = {
        'color': 'textColor',
        'background': 'backgroundColor',
        'backgroundColor': 'backgroundColor',
        'fontSize': 'fontSize',
        'fontWeight': 'fontWeight',
        'fontFamily': 'fontFamily',
        'borderRadius': 'borderRadius',
        'padding': 'padding',
        'lineHeight': 'lineHeight'
    }
    return mapping.get(css_prop, css_prop)

def resolve_tokens(props, tokens):
    """Resolve token names to actual values"""
    resolved = {}
    
    for key, prop in props.items():
        # Handle non-dictionary values (raw strings/numbers from Figma JSON)
        if not isinstance(prop, dict):
            if isinstance(prop, str):
                if prop in tokens:
                    resolved[key] = {'token': prop, 'value': prop, 'resolved': tokens[prop]}
                elif prop.endswith('px') and ' ' not in prop:
                    resolved[key] = {'value': prop, 'normalized': int(prop.replace('px', ''))}
                else:
                    resolved[key] = {'value': prop}
            else:
                resolved[key] = {'value': prop}
        else:
            resolved[key] = dict(prop)
            
            if 'token' in prop and prop['token'] in tokens:
                resolved[key]['resolved'] = tokens[prop['token']]
            elif 'value' in prop and prop['value'] in tokens:
                resolved[key]['resolved'] = tokens[prop['value']]
                resolved[key]['token'] = prop['value']
            
            # Normalize pixel values (only for single values, not compound like "8px 12px")
            if 'value' in prop and isinstance(prop['value'], str) and prop['value'].endswith('px') and ' ' not in prop['value']:
                resolved[key]['normalized'] = int(prop['value'].replace('px', ''))
    
    return resolved

def compare_components(figma_props, pr_props, component_name):
    """Compare Figma specs against PR implementation"""
    issues = []
    
    for key, figma_val in figma_props.items():
        pr_val = pr_props.get(key)
        
        if not pr_val:
            # Handle missing properties
            if key == 'imageAltRequired' and figma_val is True:
                issues.append({
                    'severity': 'MAJOR',
                    'property': 'alt',
                    'figma': {'required': True},
                    'pr': {'missing': True},
                    'recommendation': 'Add alt attribute for accessibility compliance',
                    'category': 'ACCESSIBILITY_VIOLATION'
                })
            elif 'hover' in key or 'focus' in key:
                # Skip interaction states
                continue
            else:
                issues.append({
                    'severity': 'MINOR',
                    'property': key,
                    'figma': {'value': figma_val},
                    'pr': {'missing': True},
                    'recommendation': f'Add missing property: {key}',
                    'category': 'MISSING_PROPERTY'
                })
            continue
        
        # Token comparison
        if isinstance(figma_val, dict) and isinstance(pr_val, dict):
            if figma_val.get('token') and pr_val.get('token'):
                if figma_val['token'] != pr_val['token']:
                    issues.append({
                        'severity': 'MAJOR',
                        'property': key,
                        'figma': {'token': figma_val['token'], 'value': figma_val.get('resolved')},
                        'pr': {'token': pr_val['token'], 'value': pr_val.get('resolved')},
                        'recommendation': f"Update PR to use '{figma_val['token']}' token instead of '{pr_val['token']}'",
                        'category': 'TOKEN_MISMATCH'
                    })
            
            # Value comparison
            elif figma_val.get('normalized') is not None and pr_val.get('normalized') is not None:
                diff = abs(figma_val['normalized'] - pr_val['normalized'])
                if diff > 0:
                    severity = 'MAJOR' if diff > 2 else 'MINOR'
                    issues.append({
                        'severity': severity,
                        'property': key,
                        'figma': {'value': figma_val.get('value')},
                        'pr': {'value': pr_val.get('value')},
                        'recommendation': f"Consider using {figma_val.get('value')} to match design spec (current: {pr_val.get('value')})",
                        'category': 'VALUE_DIFFERENCE'
                    })
            
            # Special case: borderRadius percentage vs px
            elif key == 'borderRadius':
                figma_value = figma_val.get('value')
                pr_value = pr_val.get('value')
                if figma_value == '9999px' and pr_value == '50%':
                    issues.append({
                        'severity': 'MINOR',
                        'property': key,
                        'figma': {'value': figma_value},
                        'pr': {'value': pr_value},
                        'recommendation': 'Different border-radius approach but same visual effect',
                        'category': 'IMPLEMENTATION_DIFFERENCE'
                    })
    
    return issues

def process_design_review():
    """Main processing function"""
    print("🔍 Starting Design Review Analysis...\n")
    
    # Load tokens
    tokens = load_json('./data/token.json')
    if not tokens:
        print("Failed to load tokens.json")
        return
    
    components = ['dropdown', 'button', 'avatar', 'header', 'checkbox']
    
    parsed_values = {
        'timestamp': datetime.now().isoformat(),
        'components': {}
    }
    
    diff_result = {
        'metadata': {
            'timestamp': datetime.now().isoformat(),
            'totalComponents': len(components),
            'totalIssues': 0,
            'summary': {'major': 0, 'minor': 0, 'warnings': 0}
        },
        'components': {}
    }
    
    # Process each component
    for component in components:
        print(f"📋 Processing {component} component...")
        
        # Load Figma spec
        figma_data = load_json(f'./data/figma_{component}.json')
        if not figma_data:
            continue
            
        # Load PR implementation
        pr_content = load_jsx(f'./data/pr_{component}.jsx')
        if not pr_content:
            continue
        
        # Parse PR props
        pr_props = parse_jsx_props(pr_content)
        
        # Resolve tokens
        figma_resolved = resolve_tokens(figma_data['props'], tokens)
        pr_resolved = resolve_tokens(pr_props, tokens)
        
        # Store parsed values
        parsed_values['components'][component] = {
            'figma': {
                'component': figma_data['component'],
                'variant': figma_data['variant'],
                'props': figma_resolved
            },
            'pr': {
                'component': figma_data['component'],
                'extractedProps': pr_resolved
            }
        }
        
        # Compare and find issues
        issues = compare_components(figma_resolved, pr_resolved, component)
        
        # Update summary counts
        for issue in issues:
            diff_result['metadata']['totalIssues'] += 1
            if issue['severity'] == 'MAJOR':
                diff_result['metadata']['summary']['major'] += 1
            elif issue['severity'] == 'MINOR':
                diff_result['metadata']['summary']['minor'] += 1
            else:
                diff_result['metadata']['summary']['warnings'] += 1
        
        diff_result['components'][component] = {
            'status': 'ISSUES_FOUND' if issues else 'PERFECT_MATCH',
            'issues': issues
        }
        
        print(f"   {'❌' if issues else '✅'} {len(issues)} issue(s) found")
    
    # Create output directory
    os.makedirs('./output', exist_ok=True)
    
    # Write results
    with open('./output/parsed_values.json', 'w') as f:
        json.dump(parsed_values, f, indent=2)
    
    with open('./output/diff_result.json', 'w') as f:
        json.dump(diff_result, f, indent=2)
    
    print('\n✨ Analysis Complete!')
    print(f"📊 Total Issues: {diff_result['metadata']['totalIssues']}")
    print(f"   🔴 Major: {diff_result['metadata']['summary']['major']}")
    print(f"   🟡 Minor: {diff_result['metadata']['summary']['minor']}")
    print(f"   ⚪ Warnings: {diff_result['metadata']['summary']['warnings']}")
    print('\n📁 Results saved to:')
    print('   - output/parsed_values.json')
    print('   - output/diff_result.json')

if __name__ == '__main__':
    process_design_review()
