#!/usr/bin/env python3
"""
skill_converter.py — Verify and convert StarRocks debug skills.

Directory structure:
  guides/                    # Cross-skill cascade guides
  references/                # Shared reference docs (common commands)
  <skill>/SKILL.md           # Skill document (main entry point)
  <skill>/references/        # Per-skill focused reference docs and cases
  <skill>/scripts/           # Skill-specific scripts (optional)
  scripts/                   # Utility scripts (this file)

Modes:
  --sync        Verify directory structure and update .idea/ config
  --convert     Convert skills to a specific IDE format

Usage:
  python scripts/skill_converter.py --sync
  python scripts/skill_converter.py --convert --ide cursor --output .cursor/
"""

import argparse
import json
import re
import yaml
from pathlib import Path
from typing import Dict, List, Optional


# Skill categories
SKILL_CATEGORIES = [
    'query',
    'import',
    'node',
    'materialized-view',
    'data-lake',
    'shared-data',
    'tablet',
    'deployment',
    'high-concurrency',
    'resource-isolation',
    'balance',
    'compaction',
    'cpu-saturation',
]


def parse_frontmatter(content: str) -> tuple[Dict, str]:
    """Parse YAML frontmatter from content."""
    match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
    if match:
        frontmatter = yaml.safe_load(match.group(1)) or {}
        body = content[match.end():]
    else:
        frontmatter = {}
        body = content
    return frontmatter, body


def extract_related_cases(content: str) -> List[str]:
    """Extract case references from Related Cases section."""
    cases = []
    match = re.search(r'##\s*Related\s*Cases\s*\n(.*?)(?:\n##|\n---|\Z)', content, re.DOTALL)
    if match:
        section = match.group(1)
        pattern = r'(?:`?case-\d+-[\w-]+`?|\[case-\d+.*?\])'
        found = re.findall(pattern, section)
        for case in found:
            clean = case.replace('`', '').replace('[', '').replace(']', '').split('—')[0].strip()
            if clean.startswith('case-'):
                cases.append(clean)
    return cases


def parse_skill(skill_dir: Path) -> Optional[Dict]:
    """Parse SKILL.md from skill directory."""
    skill_file = skill_dir / 'SKILL.md'
    if not skill_file.exists():
        return None

    content = skill_file.read_text(encoding='utf-8')
    frontmatter, body = parse_frontmatter(content)

    # Get references files
    references = []
    refs_dir = skill_dir / 'references'
    if refs_dir.exists():
        for ref_file in sorted(refs_dir.glob('*.md')):
            references.append(ref_file.name)

    # Get scripts files
    scripts = []
    scripts_dir = skill_dir / 'scripts'
    if scripts_dir.exists():
        for script_file in sorted(scripts_dir.glob('*')):
            if script_file.is_file():
                scripts.append(script_file.name)

    return {
        'name': skill_dir.name,
        'path': str(skill_dir.relative_to(skill_dir.parent)),
        'frontmatter': frontmatter,
        'content': body,
        'related_cases': extract_related_cases(body),
        'references': references,
        'scripts': scripts,
    }


def load_all_skills(project_dir: Path) -> List[Dict]:
    """Load all skills from project directory."""
    skills = []
    for category in SKILL_CATEGORIES:
        skill_dir = project_dir / category
        skill = parse_skill(skill_dir)
        if skill:
            skills.append(skill)
    return skills


def sync_skills(project_dir: Path):
    """Verify structure and update .idea/ config."""
    print("StarRocks Debug Skills — Sync")
    print("=" * 50)
    print()

    skills = load_all_skills(project_dir)

    # Verify structure
    print("Verifying skill directories...")
    for skill in skills:
        name = skill['name']
        refs = len(skill['references'])
        scripts = len(skill['scripts'])
        cases = len(skill['related_cases'])
        print(f"  ✓ {name}: {refs} refs, {scripts} scripts, {cases} related cases")

    # Check top-level shared directories
    print()
    print("Checking shared directories...")
    guides_dir = project_dir / 'guides'
    if guides_dir.exists():
        guides = list(guides_dir.glob('*.md'))
        print(f"  ✓ guides/: {len(guides)} guides")
    else:
        print("  ✗ guides/: missing")

    refs_dir = project_dir / 'references'
    if refs_dir.exists():
        shared_refs = list(refs_dir.glob('*.md'))
        print(f"  ✓ references/: {len(shared_refs)} shared docs")
    else:
        print("  ✗ references/: missing")

    print()

    # Update .idea/starrocks-debug-skills.json
    idea_dir = project_dir / '.idea'
    idea_dir.mkdir(exist_ok=True)

    config = {
        'version': 1,
        'project': 'starrocks-debug-skills',
        'skills': [
            {
                'name': s['name'],
                'keywords': s['frontmatter'].get('keywords', []),
                'references': s['references'],
                'scripts': s['scripts']
            }
            for s in skills
        ]
    }

    config_file = idea_dir / 'starrocks-debug-skills.json'
    config_file.write_text(json.dumps(config, indent=2))
    print("  ✓ Updated .idea/starrocks-debug-skills.json")

    # Summary
    print()
    print("Summary:")
    print(f"  Skills: {len(skills)}")
    total_refs = sum(len(s['references']) for s in skills)
    total_scripts = sum(len(s['scripts']) for s in skills)
    print(f"  Total per-skill references: {total_refs}")
    print(f"  Total scripts: {total_scripts}")
    print()
    print("Sync complete!")


def convert_to_cursor(skill: Dict) -> str:
    """Convert skill to Cursor .mdc format."""
    title = skill['name'].upper().replace('-', ' ')
    return f"# {title} Troubleshooting\n\n{skill['content']}"


def convert_cursor(skills: List[Dict], output_dir: Path, source_dir: Path):
    """Convert skills to Cursor format."""
    output_dir.mkdir(parents=True, exist_ok=True)

    rules_dir = output_dir / 'rules'
    rules_dir.mkdir(exist_ok=True)

    for skill in skills:
        output_file = rules_dir / f"{skill['name']}.mdc"
        output_file.write_text(convert_to_cursor(skill))

    config = {
        'version': 1,
        'skills': [s['name'] for s in skills],
        'structure': {
            'skill': 'SKILL.md',
            'references': 'references/',
            'scripts': 'scripts/'
        }
    }

    config_file = output_dir / 'cursor.json'
    config_file.write_text(json.dumps(config, indent=2))

    print(f"Converted {len(skills)} skills to {output_dir}")


def convert_claude_code(skills: List[Dict], output_dir: Path, source_dir: Path):
    """Convert skills to Claude Code format (copy full structure)."""
    import shutil

    output_dir.mkdir(parents=True, exist_ok=True)

    for skill in skills:
        skill_name = skill['name']
        src_dir = source_dir / skill_name
        dest_dir = output_dir / skill_name
        dest_dir.mkdir(exist_ok=True)

        # Copy SKILL.md
        shutil.copy2(src_dir / 'SKILL.md', dest_dir / 'SKILL.md')

        # Copy references/
        if (src_dir / 'references').exists():
            dest_refs = dest_dir / 'references'
            dest_refs.mkdir(exist_ok=True)
            for ref_file in (src_dir / 'references').glob('*.md'):
                shutil.copy2(ref_file, dest_refs / ref_file.name)

        # Copy scripts/
        if (src_dir / 'scripts').exists():
            dest_scripts = dest_dir / 'scripts'
            dest_scripts.mkdir(exist_ok=True)
            for script_file in (src_dir / 'scripts').glob('*'):
                if script_file.is_file():
                    shutil.copy2(script_file, dest_scripts / script_file.name)

    # Copy top-level shared directories
    for shared_dir in ('guides', 'references'):
        src = source_dir / shared_dir
        if src.exists():
            dest = output_dir / shared_dir
            dest.mkdir(exist_ok=True)
            for f in src.glob('*.md'):
                shutil.copy2(f, dest / f.name)

    # Copy README
    if (source_dir / 'README.md').exists():
        shutil.copy2(source_dir / 'README.md', output_dir / 'README.md')

    print(f"Converted {len(skills)} skills to {output_dir}")


def run_convert_mode(ide: str, output_dir: Path, source_dir: Path):
    """Run convert mode."""
    skills = load_all_skills(source_dir)
    print(f"Loaded {len(skills)} skills")

    all_refs = sum(len(s['references']) for s in skills)
    all_scripts = sum(len(s['scripts']) for s in skills)
    print(f"Found {all_refs} per-skill references, {all_scripts} scripts")
    print()

    if ide == 'cursor':
        convert_cursor(skills, output_dir, source_dir)
    elif ide == 'claude-code':
        convert_claude_code(skills, output_dir, source_dir)
    else:
        print(f"IDE '{ide}' not supported")

    print("\nConversion complete!")


def main():
    parser = argparse.ArgumentParser(description='Verify and convert StarRocks debug skills')

    parser.add_argument('--sync', action='store_true',
                        help='Sync mode: verify structure and update .idea/ config')
    parser.add_argument('--convert', action='store_true',
                        help='Convert mode: output to specific IDE format')
    parser.add_argument('--ide',
                        choices=['cursor', 'claude-code'],
                        help='Target IDE format (for --convert)')
    parser.add_argument('--output',
                        help='Output directory (for --convert)')

    args = parser.parse_args()

    project_dir = Path(__file__).parent.parent

    if args.sync:
        sync_skills(project_dir)
    elif args.convert:
        if not args.ide or not args.output:
            print("Error: --ide and --output are required for --convert mode")
            print("\nUsage:")
            print("  python scripts/skill_converter.py --convert --ide cursor --output .cursor/")
            return
        run_convert_mode(args.ide, Path(args.output), project_dir)
    else:
        print("Error: specify --sync or --convert")
        print("\nUsage:")
        print("  python scripts/skill_converter.py --sync")
        print("  python scripts/skill_converter.py --convert --ide cursor --output .cursor/")


if __name__ == '__main__':
    main()
