# Sprint Change Proposal
**Date:** 2026-02-27
**Author:** Dwarakadas
**Scope Classification:** Minor
**Status:** Approved

---

## Section 1: Issue Summary

**Problem Statement:**
The initial research phase hardcoded an assumption of "10 speakers" as a fixed, known quantity for the Lecture Content Organizer project. The actual requirement is that the system must support **any number of speakers** — the speaker count is not known in advance and may grow over time.

**Discovery Context:**
Identified during the early pre-implementation planning phase, before any PRD, architecture, or epics were created. The assumption appeared in the technical research document for the yt-dlp download strategy.

**Evidence:**
- Frontmatter `research_goals` field: *"~1000 lectures, 10 speakers"*
- Research body scope statement: *"~1000 lectures, 10 speakers"*
- Deduplication recommendation: *"A single shared archive across all 10 speakers' channel downloads"*

---

## Section 2: Impact Analysis

**Epic Impact:** N/A — No epics exist yet (project is pre-implementation)

**Story Impact:** N/A — No stories exist yet

**Artifact Conflicts:**

| Artifact | Impact | Action Required |
|----------|--------|-----------------|
| `research/technical-yt-dlp-channel-playlist-download-strategy-research-2026-02-27.md` | Hardcoded "10 speakers" assumption in scope and recommendations | Update to "any number of speakers" — ✓ Done |
| PRD | Not yet created | Must be authored with speaker-count-agnostic assumptions |
| Architecture | Not yet created | Must design for dynamic/configurable speaker/channel list |
| Epics & Stories | Not yet created | Must be written without fixed-speaker-count assumptions |

**Technical Impact:**
The core download strategy (yt-dlp + `--download-archive` + batch file) scales naturally to any number of speakers. The primary implication is that the **channel list must be dynamically managed** (not a static `channels.txt` with 10 entries), and any tooling that iterates over speakers must treat the count as a runtime variable, not a constant.

---

## Section 3: Recommended Approach

**Selected Path:** Direct Adjustment (Option 1)

**Rationale:**
- The issue was caught early (research phase only) before any PRD, architecture, or implementation artifacts were created
- The underlying technology choice (yt-dlp) and strategy (archive-based deduplication, batch downloads) are fully compatible with N speakers — only the framing assumed a fixed count
- No rollback or MVP re-scope is needed; this is a clarification of scope, not a reduction or pivot
- Effort: **Low** | Risk: **Low** | Timeline impact: **None**

---

## Section 4: Detailed Change Proposals

### Research Document Changes

**Artifact:** `_bmad-output/planning-artifacts/research/technical-yt-dlp-channel-playlist-download-strategy-research-2026-02-27.md`

**Change 1 — Frontmatter scope (✓ Applied)**
```
OLD: research_goals: '...for bulk transcription preprocessing (~1000 lectures, 10 speakers)'
NEW: research_goals: '...for bulk transcription preprocessing (~1000 lectures, any number of speakers with dynamically managed channel list)'
```

**Change 2 — Scope confirmation section (✓ Applied)**
```
OLD: Research Goals: ...bulk transcription preprocessing (~1000 lectures, 10 speakers)
NEW: Research Goals: ...bulk transcription preprocessing (~1000 lectures, any number of speakers with dynamically managed channel list)
```

**Change 3 — Deduplication recommendation (✓ Applied)**
```
OLD: "A single shared archive across all 10 speakers' channel downloads is the recommended approach for global deduplication"
NEW: "A single shared archive across all speakers' channel downloads is the recommended approach for global deduplication (scales to any number of speakers)"
```

### Forward-Looking Constraints (for future artifacts)

The following must be incorporated when creating future planning artifacts:

1. **PRD:** Speaker/channel list is a runtime configuration, not a hardcoded constant
2. **Architecture:** Channel management module must support dynamic addition/removal of speaker channels
3. **Stories:** Any story referencing speaker iteration must treat count as variable (e.g., "for each configured speaker" not "for each of the 10 speakers")

---

## Section 5: Implementation Handoff

**Change Scope Classification:** Minor

**Handoff:** Development team / Planning team can proceed directly.

**Responsibilities:**
- All three research document edits have been applied ✓
- Future artifact authors (PRD, Architecture, Epics) must carry forward the N-speakers assumption
- No backlog reorganization or PM/Architect escalation required

**Success Criteria:**
- No planning artifact contains a hardcoded speaker count
- Channel list management is treated as a dynamic, configurable concern in all future architecture and implementation decisions
- The download strategy remains speaker-count-agnostic throughout the project lifecycle
