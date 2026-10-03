# Desktop library redesign

Mode: Operate. The user chose a light, calm interface and asked to improve the
Windows caption controls as well as the library. Source guidance: the
[taste-skill redesign instructions](https://github.com/Leonxlnx/taste-skill/blob/main/skills/redesign-skill/SKILL.md).

## Direction contract

THESIS: A readable local lecture archive. The current blue caption, dark nav,
small type and competing import actions are the anti-reference.

OWN-WORLD: Cool white and pale grey surfaces, graphite primary controls, green
only for completed work. System text faces at readable sizes. Controls use a
7px radius, grouped rows have flat white surfaces and single separators.

STORY: Find a lecture, read its preparation state, take its next action. Keep
rename, folder and trash operations available. Preserve all stored content.

FIRST VIEWPORT: A native white Windows caption above a 64px white navigation
strip. Brand and two navigation tabs on the left, one import action on the
right. A left-aligned library heading, labelled search, aligned filters,
readable lecture rows and quiet storage information below.

FORM: Code-led native desktop redesign within Tk/ttk. No framework migration.
The signature interaction is the selected navigation surface and consistent
hover/pressed/focus feedback. Preserve native resizing, Snap and keyboard
window commands. Windows 10 falls back to its system caption.

FINISH: unreviewed and undocumented is unfinished; this build ends with the
finish review, the verdict, DESIGN.md, and every shipping raster carrying its
provenance.
