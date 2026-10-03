---
name: "Конспект"
description: "A calm, light native workspace for a local lecture archive."
colors:
  canvas: "#F7F8FA"
  surface: "#FFFFFF"
  surface-soft: "#F0F2F5"
  ink: "#24272D"
  muted: "#5B616D"
  faint: "#686F7A"
  line: "#E2E5EA"
  primary: "#303640"
  primary-hover: "#424A57"
  primary-pressed: "#222730"
  primary-soft: "#E9EDF2"
  focus: "#B45309"
  success: "#23654E"
  success-soft: "#EAF4EE"
  danger: "#B4233C"
  danger-hover: "#FAECEE"
  danger-pressed: "#F5DDE1"
  navigation-ink: "#343A45"
typography:
  display:
    fontFamily: "Segoe UI Variable Text, Segoe UI Variable, Segoe UI, Helvetica Neue, DejaVu Sans, Arial, sans-serif"
    fontSize: "28pt"
    fontWeight: 700
  headline:
    fontFamily: "Segoe UI Variable Text, Segoe UI Variable, Segoe UI, Helvetica Neue, DejaVu Sans, Arial, sans-serif"
    fontSize: "17pt"
    fontWeight: 700
  title:
    fontFamily: "Segoe UI Variable Text, Segoe UI Variable, Segoe UI, Helvetica Neue, DejaVu Sans, Arial, sans-serif"
    fontSize: "12pt"
    fontWeight: 700
  body:
    fontFamily: "Segoe UI Variable Text, Segoe UI Variable, Segoe UI, Helvetica Neue, DejaVu Sans, Arial, sans-serif"
    fontSize: "11pt"
    fontWeight: 400
  body-strong:
    fontFamily: "Segoe UI Variable Text, Segoe UI Variable, Segoe UI, Helvetica Neue, DejaVu Sans, Arial, sans-serif"
    fontSize: "11pt"
    fontWeight: 700
  label:
    fontFamily: "Segoe UI Variable Text, Segoe UI Variable, Segoe UI, Helvetica Neue, DejaVu Sans, Arial, sans-serif"
    fontSize: "10pt"
    fontWeight: 400
rounded:
  control: "7px"
  flat: "0px"
spacing:
  compact: "4px"
  label-gap: "6px"
  field-gap: "10px"
  control-inline: "12px"
  group-gap: "14px"
  row-block: "18px"
  panel-inline: "24px"
  navigation-inline: "28px"
  library-inline: "40px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.surface}"
    typography: "{typography.body-strong}"
    rounded: "{rounded.control}"
    padding: "4px 12px"
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
    textColor: "{colors.surface}"
  button-primary-active:
    backgroundColor: "{colors.primary-pressed}"
    textColor: "{colors.surface}"
  button-header:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.surface}"
    typography: "{typography.body-strong}"
    rounded: "{rounded.control}"
    padding: "3px 14px"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.body-strong}"
    rounded: "{rounded.control}"
    padding: "4px 12px"
  button-link:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.muted}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "2px 5px"
  button-danger:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.danger}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "2px 5px"
  navigation:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.navigation-ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "2px 12px"
  navigation-selected:
    backgroundColor: "{colors.primary-soft}"
    textColor: "{colors.ink}"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "4px 6px"
  select:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "3px 6px"
  status-ready:
    backgroundColor: "{colors.success-soft}"
    textColor: "{colors.success}"
    typography: "{typography.label}"
    rounded: "{rounded.flat}"
    padding: "4px 10px"
  lecture-row:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.flat}"
    padding: "18px 24px"
---

# Design System: Конспект

## Overview

**Creative North Star: "Светлый и спокойный"**

A readable local lecture archive expressed through cool white, pale grey, and graphite. The user's chosen direction is calm and light: clear priorities, readable system typography, and quiet surfaces support focused study.

The implemented application remains native Tk/ttk. Flat groups and separators organize material; rounded controls provide consistent hover, pressed, selected, and keyboard-focus feedback. The source of truth is `src/konspekt/app.py`, `ui_surfaces.py`, and `window_chrome.py`; the accompanying HTML snippets are illustrative panel previews, not application components.

**Key Characteristics:**

- Cool white and pale grey surfaces.
- Graphite primary actions with readable system type.
- Flat lecture rows with one-pixel separators.
- Green confirms completed work; amber identifies keyboard focus.
- Native desktop controls and window behavior.

## Colors

The palette is predominantly neutral, with semantic color reserved for completion, focus, and errors. Frontmatter contains the extracted values; hyphenated names map to the source palette's underscore names.

### Primary

- **Graphite** (`primary`): next actions, import, and processing progress. Its lighter hover and darker pressed tones establish control feedback.
- **Pale graphite** (`primary-soft`): selected navigation, selected list content, and progress troughs.

### Neutral

- **Cool canvas** (`canvas`): the workspace behind content.
- **White paper** (`surface`): navigation, fields, grouped lecture rows, and content panels. The source's `sidebar` is also white.
- **Soft grey** (`surface-soft`): secondary hover, disabled control surfaces, source markers, and waiting states.
- **Ink** (`ink`): primary content and selected navigation text.
- **Quiet text** (`muted`): descriptions, dates, local storage information, and ordinary management actions.
- **Secondary grey** (`faint`): disabled text and dropdown arrows; also the source's `sidebar_muted`.
- **Separator grey** (`line`): one-pixel dividers and secondary control outlines; also `sidebar_line`.
- **Navigation graphite** (`navigation-ink`): unselected navigation and brand text.

### Semantic colors

- **Completion green** (`success`, `success-soft`): successful processing and completed preparation states, backed by explicit text.
- **Focus amber** (`focus`): keyboard-focus outlines on custom control surfaces.
- **Error crimson** (`danger`): errors and explicit destructive controls. Pale crimson hover and pressed surfaces apply to the danger-link variant.

**The State Color Rule.** Keep primary actions graphite. Reserve green for completed work and retain a text label alongside semantic color.

## Typography

**Display and body font:** the same locally available system family. On macOS, Helvetica Neue takes priority. Otherwise the implementation tries Segoe UI Variable Text, Segoe UI Variable, the platform preference (Segoe UI on Windows; DejaVu Sans elsewhere), Helvetica Neue, Segoe UI, Arial, then TkDefaultFont. The frontmatter stack is a portable description; Tk chooses a single installed family rather than evaluating CSS.

Positive Tk font sizes are points, not pixels. Preserve that distinction when implementing or comparing browser previews. Native font metrics determine line height; the application defines no shared line-height or letter-spacing tokens.

### Hierarchy

- **Display:** screen titles; source role `title`.
- **Headline:** panel headings and empty-state headings; source role `heading`.
- **Title:** lecture names and subsection headings; source role `subheading`.
- **Body:** explanatory text, navigation, and fields.
- **Body strong:** primary and secondary action labels.
- **Label:** dates, search labels, storage details, statuses, and management links; source roles `small` and `secondary`.

**The Native Type Rule.** Use the selected system family throughout the desktop UI and retain the point-sized hierarchy.

## Layout

The root opens at (1240 × 780px) with a minimum size of (960 × 660px). A fixed-height white navigation strip (64px) sits above an expanding workspace. Brand and two navigation controls occupy the left; the import action sits on the right. These are desktop constraints, not responsive web breakpoints.

The library screen uses padding (40px left/right, 34px top, 32px bottom). Its labelled search expands horizontally; filters align below it with a flexible gap before sorting and trash. The scrollable row group occupies the remaining height, while local storage information sits below.

Lecture rows use panel-inline and row-block spacing. A source marker sits left, title and date expand in the middle, and status plus next action align right. Management actions form a quieter lower row. Titles wrap to the measured available width. One-pixel separators are inset by panel-inline spacing. The scrollbar hides when everything fits.

Other screens keep the same full-width workspace and use source-specific outer padding, generally (36–48px horizontally). There is no global CSS grid, mobile layout, or defined breakpoint scale.

## Elevation & Depth

The application uses tonal layering and thin separators, with no application-drawn drop shadows. White content sits on the cool canvas; focus and selected state are expressed with outlines or fill. OS window shadows and caption geometry remain platform-owned.

**The Flat Surface Rule.** Keep content surfaces flat; communicate hierarchy through type, spacing, tonal fill, and separators.

## Shapes

Buttons, text fields, and dropdowns share the control radius. Containers, lecture rows, source markers, and status labels remain rectangular. Secondary controls and fields use a one-pixel outline; primary controls use a solid fill.

The rounded control surfaces are created at runtime in `ui_surfaces.py` using Pillow's local rectangle primitives: a (24 × 24px) nine-slice source is drawn at four times resolution and downsampled. Transparent exterior corners are composited against the actual parent surface. These are generated in-code UI bitmaps, with no external art and no newly shipped raster files. Existing application/brand icons are separate assets, not produced by this redesign.

## Components

### Buttons

Primary and header actions use white text on graphite with lighter hover and darker pressed fills. Secondary actions use ink on white with a grey outline, soft-grey hover, and separator-grey press. The header variant has its own compact padding.

Management links use label typography and muted text with no resting outline, soft-grey hover, and separator-grey press. Danger links use crimson text and pale-crimson interaction surfaces. Keyboard focus uses a one-pixel amber outline. Disabled surfaces are soft grey with secondary-grey text. SurfaceButton retains ttk activation and keyboard behavior.

### Inputs / Fields

Text fields and dropdowns use white fill, ink text, a separator-grey outline, and amber focus outlines supplied by the image element. Entries keep the native caret and text editing; readonly dropdowns keep the native popup and arrow. Disabled surfaces become soft grey. Entry and dropdown padding differ as recorded in frontmatter.

### Navigation

Navigation uses ordinary body type, white resting surfaces, soft-grey hover, and pale-graphite selected fill. Selected text uses ink. Selection and keyboard focus can coexist, with the amber outline surrounding the selected fill. The desktop strip retains both tabs and the single import action.

### Chips

Status labels are flat rectangular labels, not pill controls. Completed states use completion green on its pale background; waiting states use muted text on soft grey. Labels use compact padding and remain descriptive text rather than interactive filters.

### Cards / Containers

Lecture rows are grouped on white, divided by inset separators, and contain a source marker, wrapping title, date, status, next action, and management links. Other content panels use rectangular white surfaces with thin outlines. No rounded-card grid or decorative shadow is part of this system.

### Native window caption

On supported Windows 11 builds, DWM applies a white caption, muted caption text, separator-grey border, and OS-rounded window corners. Styling runs after initial mapping and reapplies when the root or a Toplevel maps. Unsupported Windows attributes fall back to the system frame; high-contrast mode skips this caption customization. Native minimize/maximize/close, resizing, Snap, taskbar integration, and keyboard window commands remain OS-owned.

### Screen transitions

Navigation uses a short linear cross-slide (180ms in nine frames, 18px incoming and 12px outgoing travel). The explicit `--reduce-motion` option and the Windows client-area animation preference disable the slide. Control state changes themselves are immediate.

## Do's and Don'ts

### Do:

- **Do** keep primary actions graphite and semantic states labelled.
- **Do** use the selected native system font and the point-sized hierarchy.
- **Do** retain rounded control surfaces, flat content groups, and measured title wrapping.
- **Do** preserve ttk keyboard interaction and OS window behavior.
- **Do** honor the implemented reduced-motion path and native high-contrast caption fallback.

### Don't:

- **Don't** reintroduce the dark navigation or bright blue caption from the rejected direction.
- **Don't** add playful education motifs, loud gradients, crowded card grids, or decorative animation.
- **Don't** treat illustrative HTML sidecar snippets as a migration to a browser framework.
- **Don't** replace the native caption with custom window buttons or hard-code OS corner geometry.

