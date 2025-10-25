#!/usr/bin/env python3

from flask import Flask, render_template, request, jsonify
import json
import re
from datetime import datetime

app = Flask(__name__)

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
            return {'value': value}
        else:
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
    
    return {'value': value}

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
    
    # Parse CSS style object dynamically
    style_object_pattern = r'style=\{\{([^}]+)\}\}'
    style_match = re.search(style_object_pattern, jsx_content)
    
    if style_match:
        style_content = style_match.group(1)
        css_prop_pattern = r'(\w+):\s*([^,}]+)'
        css_matches = re.findall(css_prop_pattern, style_content)
        
        for css_key, css_value in css_matches:
            clean_value = css_value.strip().strip('"\'')
            prop_name = map_css_property_name(css_key)
            props[prop_name] = parse_property_value(clean_value)
    
    return props

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
    
    return resolved

def compare_components(figma_props, pr_props, component_name):
    """Compare Figma specs against PR implementation"""
    issues = []
    
    for key, figma_val in figma_props.items():
        pr_val = pr_props.get(key)
        
        if not pr_val:
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

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/analyze', methods=['POST'])
def analyze():
    try:
        data = request.json
        
        # Validate inputs
        figma_spec_str = data.get('figmaSpec', '').strip()
        tokens_str = data.get('tokens', '').strip()
        jsx_content = data.get('jsxContent', '').strip()
        component_name = data.get('componentName', 'component')
        
        # Check for empty inputs
        if not figma_spec_str:
            return jsonify({'error': 'Figma Spec JSON cannot be empty'}), 400
        if not tokens_str:
            return jsonify({'error': 'Design Tokens JSON cannot be empty'}), 400
        if not jsx_content:
            return jsonify({'error': 'JSX Content cannot be empty'}), 400
        
        # Parse JSON inputs with error handling
        try:
            figma_spec = json.loads(figma_spec_str)
        except json.JSONDecodeError as e:
            return jsonify({'error': f'Invalid Figma Spec JSON: {str(e)}'}), 400
            
        try:
            tokens = json.loads(tokens_str)
        except json.JSONDecodeError as e:
            return jsonify({'error': f'Invalid Design Tokens JSON: {str(e)}'}), 400
        
        # Parse PR props
        pr_props = parse_jsx_props(jsx_content)
        
        # Resolve tokens
        figma_resolved = resolve_tokens(figma_spec.get('props', figma_spec), tokens)
        pr_resolved = resolve_tokens(pr_props, tokens)
        
        # Compare and find issues
        issues = compare_components(figma_resolved, pr_resolved, component_name)
        
        # Build response
        result = {
            'timestamp': datetime.now().isoformat(),
            'component': {
                'name': figma_spec.get('component', component_name),
                'status': 'ISSUES_FOUND' if issues else 'PERFECT_MATCH',
                'totalIssues': len(issues),
                'issues': issues
            },
            'parsedValues': {
                'figma': figma_resolved,
                'pr': pr_resolved
            },
            'summary': {
                'major': len([i for i in issues if i['severity'] == 'MAJOR']),
                'minor': len([i for i in issues if i['severity'] == 'MINOR']),
                'warnings': len([i for i in issues if i['severity'] == 'WARNING'])
            }
        }
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 400

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
