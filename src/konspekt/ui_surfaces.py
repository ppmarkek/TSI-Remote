"""Small, scalable image elements for the existing native ttk controls."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk


def _parent_surface_style(master: tk.Misc, base: str) -> str:
    """Composite transparent control corners onto their actual parent surface."""
    style = ttk.Style(master)
    try:
        background = str(master.cget("background"))
    except tk.TclError:
        parent_style = str(master.cget("style")) or master.winfo_class()
        background = str(style.lookup(parent_style, "background")) or "#FFFFFF"
    name = f"On{background.replace('#', '')}.{base}"
    style.configure(name, background=background)
    # The base style's state-dependent background colours would otherwise show
    # through the transparent exterior on hover/press. The image supplies fill.
    style.map(name, background=[("disabled", background), ("!disabled", background)])
    return name


class SurfaceButton(ttk.Button):
    """Ordinary ttk button with its rounded exterior matched to its parent."""

    def __init__(self, master: tk.Misc, **options) -> None:
        options["style"] = _parent_surface_style(master, options.get("style", "Secondary.TButton"))
        super().__init__(master, **options)

    def configure(self, cnf=None, **options):
        if isinstance(cnf, dict) and "style" in cnf:
            cnf = dict(cnf)
            cnf["style"] = _parent_surface_style(self.master, cnf["style"])
        if "style" in options:
            options["style"] = _parent_surface_style(self.master, options["style"])
        return super().configure(cnf, **options)

    config = configure


class SurfaceEntry(ttk.Entry):
    """Ordinary ttk entry with the same parent-aware exterior."""

    def __init__(self, master: tk.Misc, **options) -> None:
        options["style"] = _parent_surface_style(master, options.get("style", "Source.TEntry"))
        super().__init__(master, **options)


class SurfaceCombobox(ttk.Combobox):
    """Ordinary ttk dropdown with the same parent-aware exterior."""

    def __init__(self, master: tk.Misc, **options) -> None:
        options["style"] = _parent_surface_style(master, options.get("style", "Settings.TCombobox"))
        super().__init__(master, **options)


def install_control_surfaces(
    master: tk.Misc, style: ttk.Style, palette: dict[str, str]
) -> list[ImageTk.PhotoImage]:
    """Give ttk its own rounded surfaces without replacing button behaviour.

    Nine-slice image elements preserve the native widget's keyboard bindings,
    focus, disabled state and geometry. Keep the returned images alive for the
    lifetime of the Tk interpreter.
    """
    images: list[ImageTk.PhotoImage] = []
    radius = 7
    dimension = 24
    scale = 4

    def surface(fill: str, outline: str | None = None) -> ImageTk.PhotoImage:
        bitmap = Image.new("RGBA", (dimension * scale, dimension * scale))
        draw = ImageDraw.Draw(bitmap)
        draw.rounded_rectangle(
            (0, 0, dimension * scale - 1, dimension * scale - 1),
            radius=radius * scale,
            fill=fill,
            outline=outline,
            width=scale if outline else 0,
        )
        image = ImageTk.PhotoImage(
            bitmap.resize((dimension, dimension), Image.Resampling.LANCZOS), master=master
        )
        images.append(image)
        return image

    button_specs = {
        "Primary": (palette["primary"], palette["primary_hover"], palette["primary_pressed"], None),
        "Secondary": (
            palette["surface"],
            palette["surface_soft"],
            palette["line"],
            palette["line"],
        ),
        "Link": (palette["surface"], palette["surface_soft"], palette["line"], None),
        "DangerLink": (palette["surface"], "#FAECEE", "#F5DDE1", None),
        "Nav": (palette["sidebar"], palette["sidebar_hover"], palette["sidebar_active"], None),
        "SidebarPrimary": (
            palette["primary"],
            palette["primary_hover"],
            palette["primary_pressed"],
            None,
        ),
    }
    for name, (normal, hover, pressed, outline) in button_specs.items():
        element = f"Konspekt.{name}.border"
        style.element_create(
            element,
            "image",
            surface(normal, outline),
            ("disabled", surface(palette["surface_soft"], outline)),
            ("pressed", "!disabled", surface(pressed, outline)),
            ("selected", "focus", surface(palette["sidebar_active"], palette["focus"])),
            ("focus", surface(normal, palette["focus"])),
            ("selected", surface(palette["sidebar_active"])),
            ("active", "!disabled", surface(hover, outline)),
            border=radius + 1,
            sticky="nsew",
        )
        style.layout(
            f"{name}.TButton",
            [
                (
                    element,
                    {
                        "sticky": "nsew",
                        "children": [
                            (
                                "Button.padding",
                                {
                                    "sticky": "nsew",
                                    "children": [("Button.label", {"sticky": "nsew"})],
                                },
                            )
                        ],
                    },
                )
            ],
        )

    for name, prefix in (("Source.TEntry", "Entry"), ("Settings.TCombobox", "Combobox")):
        element = f"Konspekt.{prefix}.border"
        style.element_create(
            element,
            "image",
            surface(palette["surface"], palette["line"]),
            ("disabled", surface(palette["surface_soft"], palette["line"])),
            ("focus", surface(palette["surface"], palette["focus"])),
            border=radius + 1,
            sticky="nsew",
        )
        children = []
        if prefix == "Combobox":
            children.append(("Combobox.downarrow", {"side": "right", "sticky": "ns"}))
        children.append(
            (
                f"{prefix}.padding",
                {"sticky": "nsew", "children": [(f"{prefix}.textarea", {"sticky": "nsew"})]},
            )
        )
        style.layout(name, [(element, {"sticky": "nsew", "children": children})])

    return images
