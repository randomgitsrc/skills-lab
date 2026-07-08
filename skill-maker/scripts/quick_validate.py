#!/usr/bin/env python3
"""Quick validation script for skills."""

import sys
import re
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("Error: PyYAML required. Install: pip install pyyaml")


def validate_skill(skill_path):
    skill_path = Path(skill_path)
    skill_md = skill_path / 'SKILL.md'
    if not skill_md.exists():
        return False, "SKILL.md not found"

    content = skill_md.read_text()
    if not content.startswith('---'):
        return False, "No YAML frontmatter found"

    match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
    if not match:
        return False, "Invalid frontmatter format"

    try:
        frontmatter = yaml.safe_load(match.group(1))
        if not isinstance(frontmatter, dict):
            return False, "Frontmatter must be a YAML dictionary"
    except yaml.YAMLError as e:
        return False, f"Invalid YAML in frontmatter: {e}"

    ALLOWED = {'name', 'description', 'license', 'allowed-tools', 'metadata', 'compatibility'}
    unexpected = set(frontmatter.keys()) - ALLOWED
    if unexpected:
        return False, f"Unexpected key(s): {', '.join(sorted(unexpected))}. Allowed: {', '.join(sorted(ALLOWED))}"

    if 'name' not in frontmatter:
        return False, "Missing 'name' in frontmatter"
    if 'description' not in frontmatter:
        return False, "Missing 'description' in frontmatter"

    name = frontmatter.get('name', '')
    if not isinstance(name, str):
        return False, f"Name must be a string, got {type(name).__name__}"
    name = name.strip()
    if name:
        if not re.match(r'^[a-z0-9-]+$', name):
            return False, f"Name '{name}' should be kebab-case (lowercase, digits, hyphens)"
        if name.startswith('-') or name.endswith('-') or '--' in name:
            return False, f"Name '{name}' cannot start/end with hyphen or have consecutive hyphens"
        if len(name) > 64:
            return False, f"Name too long ({len(name)} chars). Max 64."

    description = frontmatter.get('description', '')
    if not isinstance(description, str):
        return False, f"Description must be a string, got {type(description).__name__}"
    description = description.strip()
    if description:
        if '<' in description or '>' in description:
            return False, "Description cannot contain angle brackets"
        if len(description) > 1024:
            return False, f"Description too long ({len(description)} chars). Max 1024."

    body = content[match.end():].strip()
    line_count = len(body.split('\n'))
    if line_count > 500:
        return False, f"SKILL.md body is {line_count} lines. Keep under 500; move detail to reference/."

    return True, "Skill is valid!"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python quick_validate.py <skill_directory>")
        sys.exit(1)
    valid, message = validate_skill(sys.argv[1])
    print(message)
    sys.exit(0 if valid else 1)
