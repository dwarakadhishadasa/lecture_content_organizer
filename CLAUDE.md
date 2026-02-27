# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Lecture Content Organizer** — a software project currently in the pre-implementation planning phase using the BMAD (Business Method for AI-Driven Development) framework v6.0.3.

No source code exists yet. The project is being planned through BMAD workflows before implementation begins.

## Project Structure

```
_bmad/                  # BMAD framework installation (do not modify directly)
  _config/              # Agent customization files and manifest
  bmm/                  # Business Method Module — primary planning/dev workflows
  bmb/                  # Builder module for creating BMAD agents/workflows
  cis/                  # Creative Intelligence Suite
  gds/                  # Game Dev Studio (not relevant for this project)
  tea/                  # Test Architecture Enterprise
  core/                 # Core agents and shared workflows
_bmad-output/           # Generated artifacts from BMAD workflows
  planning-artifacts/   # PRDs, product briefs, architecture docs, epics
  implementation-artifacts/ # Stories, sprint plans, code review outputs
  test-artifacts/       # Test plans and results
  bmb-creations/        # Custom agents/modules/workflows
docs/                   # Project knowledge base (referenced by BMAD agents)
.claude/commands/       # BMAD slash commands for Claude Code
```

## BMAD Workflow

This project follows the BMAD methodology. The typical progression is:

1. **Analysis**: Create product brief, run research (`/bmad-bmm-create-product-brief`, `/bmad-bmm-domain-research`)
2. **Planning**: Create PRD, UX design, architecture (`/bmad-bmm-create-prd`, `/bmad-bmm-create-ux-design`, `/bmad-bmm-create-architecture`)
3. **Readiness**: Validate specs, create epics/stories (`/bmad-bmm-check-implementation-readiness`, `/bmad-bmm-create-epics-and-stories`)
4. **Implementation**: Sprint planning, story development (`/bmad-bmm-sprint-planning`, `/bmad-bmm-dev-story`)

Key BMAD agents available via slash commands:
- `/bmad-agent-bmm-pm` — Product Manager for requirements and planning
- `/bmad-agent-bmm-architect` — Solution architecture decisions
- `/bmad-agent-bmm-dev` — Development implementation
- `/bmad-agent-bmm-qa` — Quality assurance and testing
- `/bmad-help` — Get guidance on what workflow step to do next

## Configuration

- **User**: Dwarakadas
- **Skill level**: Intermediate
- **Languages**: English (communication and documents)
- **Planning artifacts**: `_bmad-output/planning-artifacts/`
- **Implementation artifacts**: `_bmad-output/implementation-artifacts/`
- **Project knowledge**: `docs/`

## Key Conventions

- All BMAD-generated documents go in `_bmad-output/`, organized by artifact type
- Project knowledge and reference materials go in `docs/`
- Do not manually edit files inside `_bmad/` — use BMAD builder workflows to customize agents/workflows
- Agent customization is done through YAML files in `_bmad/_config/agents/`
