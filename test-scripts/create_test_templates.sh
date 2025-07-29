#!/bin/bash

# Script to create test template files for Jinja templating functionality

echo "Creating test template files..."

# Simple template
echo 'User: {{ row.name }} ({{ row.email }}) - Age: {{ row.age }}, Status: {% if row.active %}Active{% else %}Inactive{% endif %}' > /tmp/user_template.jinja
echo "Created /tmp/user_template.jinja"

# JSON template
cat > /tmp/user_json.jinja << 'EOF'
{
  "id": {{ row.id }},
  "name": "{{ row.name }}",
  "email": "{{ row.email }}",
  "age": {{ row.age }},
  "active": {{ row.active | lower }}
}
EOF
echo "Created /tmp/user_json.jinja"

# CSV template
echo '{{ row.id }},{{ row.name }},{{ row.email }},{{ row.age }},{{ row.active }}' > /tmp/user_csv.jinja
echo "Created /tmp/user_csv.jinja"

# HTML template
cat > /tmp/user_html.jinja << 'EOF'
<tr>
  <td>{{ row.id }}</td>
  <td>{{ row.name | upper }}</td>
  <td><a href="mailto:{{ row.email }}">{{ row.email }}</a></td>
  <td>{{ row.age }}</td>
  <td>{% if row.active %}<span style="color: green;">✓</span>{% else %}<span style="color: red;">✗</span>{% endif %}</td>
</tr>
EOF
echo "Created /tmp/user_html.jinja"

# Complex template with multiple features
cat > /tmp/user_card.jinja << 'EOF'
================================
User Profile #{{ row.id }}
================================
Name: {{ row.name | upper }}
Email: {{ row.email }}
Age: {{ row.age }} years old
Account Status: {% if row.active %}✓ Active{% else %}✗ Inactive{% endif %}

{% if row.age >= 30 %}
Senior Member (30+ years)
{% else %}
Junior Member
{% endif %}
EOF
echo "Created /tmp/user_card.jinja"

# Markdown template
cat > /tmp/user_markdown.jinja << 'EOF'
## {{ row.name }}

- **Email**: [{{ row.email }}](mailto:{{ row.email }})
- **Age**: {{ row.age }} years
- **Status**: {% if row.active %}🟢 Active{% else %}🔴 Inactive{% endif %}

---
EOF
echo "Created /tmp/user_markdown.jinja"

echo "All template files created successfully!"
echo ""
echo "You can now run the SQL commands to test the Jinja functionality."