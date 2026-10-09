<div align="center">
  <img src="docs/assets/second-mark.svg" width="120" alt="Second mark" /><br />
  <h1>Second's Skill Library</h1>
  <p>Reusable, documented capabilities for OpenClaw agents.</p>
  <img src="docs/assets/second-badge.svg" alt="Second · OpenClaw" />
</div>

---

## About

Each top-level directory is a self-contained skill with an uppercase `SKILL.md` entry point and YAML frontmatter. Supporting scripts and dependency files stay beside the skill they serve.

## Skill catalog

| Skill | Purpose |
| --- | --- |
| [Antigravity CLI](antigravity-cli/SKILL.md) | Drive the Antigravity CLI and verify its effects. |
| [Tapo Device Control](tapo-device-control/SKILL.md) | Discover and control supported Tapo devices. |
| [Facebook Messenger Control and Agent Bridge](facebook-messenger-control-and-agent-bridge/SKILL.md) | Operate a Messenger bridge for authorized workflows. |

## Provenance

The three modules were adopted from the retiring AISkills repository at source commit `259820deae46bb95a40d72a67161ad91bf9b37a8`. Their directories were flattened for OpenClaw discovery and their runtime guidance was updated for this library.

## Use and installation

Read the relevant `SKILL.md` before using a skill, including its prerequisites and constraints. The canonical local checkout is `~/Skills/`. From the live workspace, `bash scripts/sync_skills.sh` publishes changes to this repository, and `bash scripts/link_skills.sh` verifies and links installed skills into `skills/`.

Keep credentials and local runtime state outside this public repository.
