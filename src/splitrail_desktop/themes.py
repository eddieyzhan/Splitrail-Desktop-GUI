"""Two complete, accessible application palettes."""
from __future__ import annotations
import sys
from tkinter import font

THEMES = {
    'Pearl': dict(BG='#F5F5F7', CARD='#FFFFFF', INK='#1D1D1F', MUTED='#62626A',
                  BORDER='#DEDEE4', BLUE='#0066CC', BLUE_DARK='#0057B0', BLUE_SOFT='#EDEDF2',
                  TEAL='#087E8B', AMBER='#8A5600', AMBER_BG='#FFF1D6', RED='#C52E37',
                  RED_BG='#FFE9EB', GREEN='#237645', GREEN_BG='#E7F4EC', ON_ACCENT='#FFFFFF',
                  TOKEN_COLORS=('#0066CC', '#087E8B', '#8556B9', '#B76B24')),
    'Nord': dict(BG='#2E3440', CARD='#3B4252', INK='#ECEFF4', MUTED='#D8DEE9',
                 BORDER='#4C566A', BLUE='#88C0D0', BLUE_DARK='#5E81AC', BLUE_SOFT='#434C5E',
                 TEAL='#8FBCBB', AMBER='#EBCB8B', AMBER_BG='#434C5E', RED='#BF616A',
                 RED_BG='#434C5E', GREEN='#A3BE8C', GREEN_BG='#434C5E', ON_ACCENT='#2E3440',
                 TOKEN_COLORS=('#88C0D0', '#8FBCBB', '#B48EAD', '#D08770')),
}


def system_fonts(root) -> tuple[str, str]:
    available = set(font.families(root))
    candidates = ('.AppleSystemUIFont', 'SF Pro Text', 'Helvetica Neue') if sys.platform == 'darwin' else ('Segoe UI', 'Inter', 'DejaVu Sans')
    regular = next((name for name in candidates if name in available), 'TkDefaultFont')
    mono = next((name for name in ('SF Mono', 'Cascadia Mono', 'DejaVu Sans Mono') if name in available), regular)
    return regular, mono


def recolor(widget, previous: dict, current: dict) -> None:
    if getattr(widget, '_theme_preview', False):
        return
    mapping = {value: current[key] for key, value in previous.items() if isinstance(value, str)}
    # Shared Nord surfaces should become neutral Pearl surfaces, not tinted status backgrounds.
    mapping[previous['BLUE_SOFT']] = current['BLUE_SOFT']
    mapping[previous['BG']] = current['BG']
    mapping[previous['CARD']] = current['CARD']
    for old, new in zip(previous['TOKEN_COLORS'], current['TOKEN_COLORS']):
        mapping[old] = new
    options = ('background', 'foreground', 'highlightbackground', 'highlightcolor',
               'activebackground', 'activeforeground', 'insertbackground', 'selectbackground', 'selectforeground')
    for key in options:
        try:
            old = str(widget.cget(key))
            if old in mapping:
                widget.configure(**{key: mapping[old]})
        except Exception:
            pass
    if widget.winfo_class() == 'Canvas':
        for item in widget.find_all():
            for key in ('fill', 'outline'):
                try:
                    value = widget.itemcget(item, key)
                    if value in mapping:
                        widget.itemconfigure(item, **{key: mapping[value]})
                except Exception:
                    pass
    for child in widget.winfo_children():
        recolor(child, previous, current)


def rounded_image(root, fill: str, outline: str | None = None):
    """Small antialiased PNG, generated with the standard library for ttk's nine-slice renderer."""
    import base64, struct, zlib, tkinter as tk
    size, radius = 28, 9
    rgb = tuple(int(fill[i:i+2], 16) for i in (1, 3, 5))
    edge = tuple(int(outline[i:i+2], 16) for i in (1, 3, 5)) if outline else rgb
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            dx = max(radius - x - .5, x + .5 - (size - radius), 0)
            dy = max(radius - y - .5, y + .5 - (size - radius), 0)
            distance = (dx*dx + dy*dy)**.5
            alpha = max(0, min(1, radius - distance + .5))
            border = outline and (distance > radius-1 or x < 1 or y < 1 or x >= size-1 or y >= size-1)
            rows.extend((* (edge if border else rgb), round(alpha*255)))
    def chunk(kind, value):
        return struct.pack('!I', len(value)) + kind + value + struct.pack('!I', zlib.crc32(kind + value) & 0xffffffff)
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', size, size, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b'')
    return tk.PhotoImage(master=root, data=base64.b64encode(png))


def style_buttons(root, style, palette):
    """Rounded controls with visible keyboard focus, pressed, hover and disabled states."""
    generation = getattr(root, '_button_generation', 0) + 1
    root._button_generation = generation
    images = []
    for name, fill, ink in (('TButton', palette['BLUE_SOFT'], palette['INK']),
                            ('Quiet.TButton', palette['BLUE_SOFT'], palette['BLUE']),
                            ('Primary.TButton', palette['BLUE'], palette['ON_ACCENT']),
                            ('Alert.TButton', palette['AMBER_BG'], palette['AMBER'])):
        normal = rounded_image(root, fill)
        hover = rounded_image(root, palette['BLUE_DARK'] if name.startswith('Primary') else palette['BORDER'])
        focus = rounded_image(root, fill, palette['BLUE_DARK'])
        disabled = rounded_image(root, palette['BLUE_SOFT'])
        images.extend((normal, hover, focus, disabled))
        element = f'rounded{generation}.{name}'
        style.element_create(element, 'image', normal, ('disabled', disabled), ('pressed', hover), ('focus', focus), ('active', hover), border=8, sticky='nsew')
        style.layout(name, [(element, {'sticky': 'nsew', 'children': [('Button.padding', {'sticky': 'nsew', 'children': [('Button.label', {'sticky': 'nsew'})]})]})])
        style.configure(name, foreground=ink, padding=(10, 3), background=palette['BG'])
        style.map(name, foreground=[('disabled', palette['MUTED']), ('active', ink)], background=[])
        style.configure('Card.' + name, background=palette['CARD'])
    # ttk owns the image names; keep Python references alive across theme changes.
    root._button_images = getattr(root, '_button_images', []) + images


def match_button_surfaces(widget, card_color):
    from tkinter import ttk
    if isinstance(widget, ttk.Button):
        name = str(widget.cget('style')) or 'TButton'
        name = name.removeprefix('Card.')
        try:
            card = str(widget.master.cget('background')) == card_color
        except Exception:
            card = False
        widget.configure(style=('Card.' if card else '') + name)
    for child in widget.winfo_children():
        match_button_surfaces(child, card_color)
