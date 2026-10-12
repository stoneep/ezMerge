"""SketchUp-style "type a value" input (the VCB) with scene unit support."""

import re

import bpy

_UNITS = {
    'KILOMETERS': (1000.0, "km"),
    'METERS': (1.0, "m"),
    'CENTIMETERS': (0.01, "cm"),
    'MILLIMETERS': (0.001, "mm"),
    'MICROMETERS': (1e-6, "um"),
    'MILES': (1609.344, "mi"),
    'FEET': (0.3048, "ft"),
    'INCHES': (0.0254, "in"),
    'THOU': (0.0000254, "thou"),
}

_START_CHARS = set("0123456789.,-+(")
_CHARS = _START_CHARS | set("*/) '\";")
# Separates the two values of a rectangle: "50;30" or "50x30".
_PAIR_SEPARATORS = re.compile(r"[;xX]")


def unit_factor(unit_settings):
    """(meters per display unit, suffix) for the scene's length unit."""
    if unit_settings.system == 'NONE':
        return 1.0, ""
    return _UNITS.get(unit_settings.length_unit, (1.0, ""))


def format_length(value, unit_settings, precision=4):
    """Blender units -> text in the scene's display unit."""
    if unit_settings.system == 'NONE':
        return _trim(f"{value:.{precision}f}")
    meters = value * unit_settings.scale_length
    if unit_settings.length_unit == 'ADAPTIVE':
        return bpy.utils.units.to_string(unit_settings.system, 'LENGTH', meters, precision=precision)
    factor, suffix = unit_factor(unit_settings)
    return f"{_trim(f'{meters / factor:.{precision}f}')} {suffix}"


def parse_length(text, unit_settings):
    """Text such as "50", "5cm", "2'6\"" or "10*3" -> Blender units.

    A number without a unit uses the scene's length unit. Raises ValueError.
    """
    text = text.strip().replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        number = None
    if number is None and not _looks_complete(text):
        # Blender prints a traceback for every expression it fails to parse,
        # so skip half-typed input like "5c" or "-".
        raise ValueError(text)
    if unit_settings.system == 'NONE':
        if number is not None:
            return number
        return bpy.utils.units.to_value('NONE', 'LENGTH', text)
    _factor, suffix = unit_factor(unit_settings)
    ref = f"1{suffix}" if suffix else None
    meters = bpy.utils.units.to_value(unit_settings.system, 'LENGTH', text, str_ref_unit=ref)
    return meters / unit_settings.scale_length


_UNIT_WORDS = {
    "km", "m", "cm", "mm", "um", "µm", "nm", "pm", "kilometer", "kilometers", "meter",
    "meters", "centimeter", "centimeters", "millimeter", "millimeters",
    "mi", "mile", "miles", "ft", "foot", "feet", "in", "inch", "inches", "thou", "mil",
    "yd", "yard", "yards",
}


def _looks_complete(text):
    if not text or text.count("(") != text.count(")"):
        return False
    last = text[-1]
    if last.isdigit() or last in ")'\"":
        return True
    word = ""
    for ch in reversed(text):
        if not ch.isalpha():
            break
        word = ch + word
    return word.lower() in _UNIT_WORDS


def parse_angle(text):
    """Degrees typed as "30", "-45.5" or "30°". Raises ValueError."""
    text = text.strip().replace(",", ".").rstrip("°").strip()
    if not text:
        raise ValueError("empty")
    return float(text)


def _trim(s):
    return s.rstrip("0").rstrip(".") if "." in s else s


class NumInput:
    def __init__(self):
        self.text = ""

    @property
    def active(self):
        return bool(self.text)

    def clear(self):
        self.text = ""

    def handle(self, event):
        """Consume a key press that edits the typed value. Returns True if used."""
        if event.value != 'PRESS':
            return False
        if event.type == 'BACK_SPACE':
            if not self.text:
                return False
            self.text = "" if event.ctrl else self.text[:-1]
            return True
        ch = event.unicode
        if not ch or event.ctrl or event.alt:
            return False
        if ch in _START_CHARS or (self.text and (ch in _CHARS or ch.isalpha())):
            self.text += ch
            return True
        return False

    def raw_parts(self):
        """The typed text split at ``;`` or ``x`` without interpreting the parts."""
        return _PAIR_SEPARATORS.split(self.text) if self.text else []

    def parts(self, unit_settings):
        """The typed text split at ``;`` or ``x``, each part in Blender units (None while incomplete)."""
        if not self.text:
            return []
        values = []
        for part in _PAIR_SEPARATORS.split(self.text):
            try:
                values.append(parse_length(part, unit_settings))
            except (ValueError, SyntaxError, TypeError, ZeroDivisionError):
                values.append(None)
        return values

    def value(self, unit_settings):
        """Parsed value in Blender units, or None while the text is incomplete."""
        try:
            return parse_length(self.text, unit_settings)
        except (ValueError, SyntaxError, TypeError, ZeroDivisionError):
            return None
