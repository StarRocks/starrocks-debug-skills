#!/usr/bin/env bash
# install.sh — Install StarRocks Debug Skills to IDEs
#
# Directory structure:
#   guides/                    # Cross-skill cascade guides
#   references/                # Shared reference docs (common commands)
#   <skill>/SKILL.md           # Skill document (main entry point)
#   <skill>/references/        # Per-skill focused reference docs and cases
#   <skill>/scripts/           # Skill-specific scripts (optional)
#   scripts/                   # Utility scripts (this file)
#
# Usage:
#   ./scripts/install.sh --tool claude-code --target ~/.claude/skills/
#   ./scripts/install.sh --tool cursor --target .cursor/rules/
#   ./scripts/install.sh --tool all --target .

set -euo pipefail

TOOL=""
TARGET=""
VERBOSE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tool)
      TOOL="$2"
      shift 2
      ;;
    --target)
      TARGET="$2"
      shift 2
      ;;
    --verbose|-v)
      VERBOSE="true"
      shift
      ;;
    --help|-h)
      echo "Usage: $0 --tool <ide> --target <path>"
      echo ""
      echo "Tools:"
      echo "  claude-code    Install for Claude Code"
      echo "  cursor         Install for Cursor IDE (.mdc rules)"
      echo "  jetbrains      Install for JetBrains IDEs"
      echo "  all            Install for all supported IDEs"
      echo ""
      echo "Examples:"
      echo "  $0 --tool claude-code --target ~/.claude/skills/"
      echo "  $0 --tool cursor --target .cursor/rules/"
      echo "  $0 --tool all --target ."
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

if [ -z "$TOOL" ] || [ -z "$TARGET" ]; then
  echo "Error: --tool and --target are required"
  echo "Use --help for usage information"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Skill categories (directories with SKILL.md)
SKILLS="query import node materialized-view data-lake shared-data tablet deployment high-concurrency resource-isolation balance compaction cpu-saturation"

log() {
  if [ -n "$VERBOSE" ]; then
    echo "  $1"
  fi
}

# Install for Claude Code
# Copies skill directories with SKILL.md, references/, scripts/
# Also copies top-level guides/ and references/ shared directories
install_claude_code() {
  local target="$1"
  echo "Installing for Claude Code to: $target"

  mkdir -p "$target"

  local count=0
  for skill in $SKILLS; do
    local skill_dir="$PROJECT_DIR/$skill"
    if [ -f "$skill_dir/SKILL.md" ]; then
      mkdir -p "$target/$skill"

      # Copy SKILL.md
      cp "$skill_dir/SKILL.md" "$target/$skill/"
      log "Copied: $skill/SKILL.md"

      # Copy references/ if exists
      if [ -d "$skill_dir/references" ]; then
        mkdir -p "$target/$skill/references"
        for ref_file in "$skill_dir/references"/*.md; do
          if [ -f "$ref_file" ]; then
            cp "$ref_file" "$target/$skill/references/"
          fi
        done
        log "Copied: $skill/references/ ($(ls "$skill_dir/references"/*.md 2>/dev/null | wc -l | tr -d ' ') files)"
      fi

      # Copy scripts/ if exists
      if [ -d "$skill_dir/scripts" ]; then
        mkdir -p "$target/$skill/scripts"
        for script_file in "$skill_dir/scripts"/*; do
          if [ -f "$script_file" ]; then
            cp "$script_file" "$target/$skill/scripts/"
          fi
        done
        log "Copied: $skill/scripts/"
      fi

      count=$((count + 1))
    fi
  done

  # Copy top-level shared guides/
  if [ -d "$PROJECT_DIR/guides" ]; then
    mkdir -p "$target/guides"
    for guide_file in "$PROJECT_DIR/guides"/*.md; do
      if [ -f "$guide_file" ]; then
        cp "$guide_file" "$target/guides/"
      fi
    done
    log "Copied: guides/ ($(ls "$PROJECT_DIR/guides"/*.md 2>/dev/null | wc -l | tr -d ' ') files)"
  fi

  # Copy top-level shared references/
  if [ -d "$PROJECT_DIR/references" ]; then
    mkdir -p "$target/references"
    for ref_file in "$PROJECT_DIR/references"/*.md; do
      if [ -f "$ref_file" ]; then
        cp "$ref_file" "$target/references/"
      fi
    done
    log "Copied: references/ ($(ls "$PROJECT_DIR/references"/*.md 2>/dev/null | wc -l | tr -d ' ') files)"
  fi

  # Copy top-level README
  if [ -f "$PROJECT_DIR/README.md" ]; then
    cp "$PROJECT_DIR/README.md" "$target/"
    log "Copied: README.md"
  fi

  echo "✓ Claude Code skills installed: $count skills to $target"
}

# Install for Cursor IDE
# Converts SKILL.md to .mdc format
install_cursor() {
  local target="$1"
  echo "Installing for Cursor to: $target"

  mkdir -p "$target/rules"

  local count=0
  for skill in $SKILLS; do
    local skill_file="$PROJECT_DIR/$skill/SKILL.md"
    if [ -f "$skill_file" ]; then
      local output_file="$target/rules/${skill}.mdc"

      # Extract content after YAML frontmatter
      sed '/^---$/,/^---$/d' "$skill_file" > "$output_file"

      count=$((count + 1))
      log "Converted: $skill/SKILL.md → ${skill}.mdc"
    fi
  done

  # Create cursor.json index
  local config_file="$target/cursor.json"
  echo '{"version": 1, "skills": [' > "$config_file"

  local first=true
  for skill in $SKILLS; do
    if [ -f "$PROJECT_DIR/$skill/SKILL.md" ]; then
      if [ "$first" = true ]; then
        first=false
      else
        echo ',' >> "$config_file"
      fi
      echo "\"$skill\"" >> "$config_file"
    fi
  done

  echo '], "structure": {"skill": "SKILL.md", "references": "references/", "scripts": "scripts/"}}' >> "$config_file"

  echo "✓ Cursor rules installed: $count rules to $target/rules"
}

# Install for JetBrains IDEs
# Creates JSON config and copies skills as markdown
install_jetbrains() {
  local target="$1"
  echo "Installing for JetBrains to: $target"

  mkdir -p "$target"

  # Copy existing config if exists
  if [ -f "$PROJECT_DIR/.idea/starrocks-debug-skills.json" ]; then
    cp "$PROJECT_DIR/.idea/starrocks-debug-skills.json" "$target/"
    log "Copied: starrocks-debug-skills.json"
  fi

  # Copy skills as markdown files
  local skills_dir="$target/skills"
  mkdir -p "$skills_dir"

  local count=0
  for skill in $SKILLS; do
    if [ -f "$PROJECT_DIR/$skill/SKILL.md" ]; then
      cp "$PROJECT_DIR/$skill/SKILL.md" "$skills_dir/${skill}.md"
      count=$((count + 1))
      log "Copied: $skill/SKILL.md → ${skill}.md"
    fi
  done

  echo "✓ JetBrains config installed: $count skills to $target"
}

case "$TOOL" in
  claude-code)
    install_claude_code "$TARGET"
    ;;
  cursor)
    install_cursor "$TARGET"
    ;;
  jetbrains)
    install_jetbrains "$TARGET"
    ;;
  all)
    install_claude_code "$TARGET/.claude/skills/"
    install_cursor "$TARGET/.cursor/rules/"
    install_jetbrains "$TARGET/.idea/"
    ;;
  *)
    echo "Unknown tool: $TOOL"
    echo "Supported: claude-code, cursor, jetbrains, all"
    exit 1
    ;;
esac

echo ""
echo "Installation complete!"
