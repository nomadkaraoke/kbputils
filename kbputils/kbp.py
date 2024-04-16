import collections
import re
import string
import typing
import io
from . import validators

class KBPFile:

    DIVIDER = "-----------------------------"

    def __init__(self, kbpFile, **kwargs):
        self.pages = []
        self.images = []
        needsclosed = False
        if not isinstance(kbpFile, io.IOBase):
            kbpFile = open(kbpFile, "r", encoding="utf-8")
            needsclosed = True
        self.parse([x.rstrip() for x in kbpFile.readlines()], **kwargs)
        if needsclosed:
            kbpFile.close()

    def parse(self, kbpLines, resolve_colors=False, resolve_wipe=True):
        in_header = False
        divider = False
        for x, line in enumerate(kbpLines):
            if in_header:
                if line.startswith("'Palette Colours"):
                    self.colors = KBPPalette.from_string(kbpLines[x+1])
                elif line.startswith("'Styles"):
                    data = kbpLines[x+1:kbpLines.index("  StyleEnd", x+1)]
                    opts = {"palette": self.colors} if resolve_colors else {}
                    self.styles = KBPStyleCollection.from_textlines([x for x in data if not x.startswith("'")], **opts)
                elif line.startswith("'Margins"):
                    self.parse_margins(kbpLines[x+1])
                elif line.startswith("'Other"):
                    self.parse_other(kbpLines[x+1])
                elif line == "'--- Track Information ---":
                    data = kbpLines[x+1:kbpLines.index(KBPFile.DIVIDER, x+1)]
                    self.parse_trackinfo(data)
                    if self.trackinfo["status"] != '1':
                        raise NotImplementedError("Tracks must be synced before they can be used with kbputils.")

            elif divider and line == "PAGEV2":
                data = kbpLines[x+1:kbpLines.index(KBPFile.DIVIDER, x+1)]
                opts = {"default_wipe": self.other['wipedetail']} if resolve_wipe else {}
                self.pages.append(KBPPage.from_textlines(data, **opts))

            elif divider and line == "IMAGE":
                # TODO: Determine if it's ever possible to have multiple image lines in one section
                data = kbpLines[x+1]
                self.images.append(KBPImage.from_string(data))

            if divider and line == "HEADERV2":
                in_header = True

            if line == KBPFile.DIVIDER:
                in_header = False
                divider = True
            # Ignore empty/comment lines and still consider the previous line to be a divider
            elif line != "" and not line.startswith("'"):
                divider = False

        missing = ', '.join(filter(lambda x: not hasattr(self, x), ('colors', 'styles', 'margins', 'other','pages', 'trackinfo')))
        if missing:
            raise ValueError(f"Invalid KBP file, missing sections: {missing}")
    

    def parse_margins(self, margin_line):
        self.margins = dict(zip(("left", "right", "top", "spacing"), (int(x) for x in margin_line.strip().split(","))))

    def parse_other(self, other_line):
        self.other = dict(zip(("bordercolor", "wipedetail"), (int(x) for x in other_line.strip().split(","))))

    # This is formatted in a way that it may allow freeform key/value pairs, so
    # leaving this as just a dict, but so far it always seems to contain exactly:
    # - Status
    # - Title
    # - Artist
    # - Audio
    # - BuildFile
    # - Intro
    # - Outro
    # - Comments
    def parse_trackinfo(self, trackinfo_lines):
        self.trackinfo = {}
        prev = None
        for line in trackinfo_lines:
            if line.startswith(" "):
                self.trackinfo[prev] += f"\n{line.lstrip()}"
            elif line != "" and not line.startswith("'"):
                fields = line.split(maxsplit=1)
                fields[0] = fields[0].lower()
                if len(fields) == 1:
                    fields.append("")
                self.trackinfo[fields[0]] = fields[1]
                prev = fields[0]

    

    # Convenience method to get all the lyric text without timing info,
    # potentially with syllable marks added. To get lyric text that could be used
    # to restart sync on a new project, use:
    # myKBPFile.text(include_empty=True, syllable_separator="/", space_is_separator=True)
    def text(self, page_separator="", include_empty=False, syllable_separator="", space_is_separator=False):
        result = []
        for page in self.pages:
            lines = []
            for line in page.lines:
                if include_empty or not line.isempty():
                    lines.append(line.text(syllable_separator=syllable_separator, space_is_separator=space_is_separator))
            result.append("\n".join(lines))
        return f"\n{page_separator}\n".join(result)

# I didn't want to use a list because it can't have specific size, and liked
# all the data being tuple-compatible, but still wanted list-like access with [].
# There are modules that make something like a "frozen" list, but I didn't want
# to pull in any more extra modules, so you get this monstrosity, sorry world
@validators.validated_instantiation
class KBPPalette(collections.namedtuple("KBPPalette", tuple(range(16)), rename=True)):

    __annotations__ = dict((f"_{x}",str) for x in range(16))
    
    # namedtuple-generated classes don't have __init__ so must instead override __new__
    def __new__(cls, *colors):
        assert len(colors) == 16 and all(re.match(r"[0-9A-F]{3}$", x) for x in colors)
        return super().__new__(cls, *colors)

    # Hide the field names because they are just _0 to _15 and aren't going to be used directly
    def __repr__(self):
        return "KBPPalette(" + ", ".join(repr(x) for x in self) + ")"

    def __getitem__(self, x):
        if isinstance(x, int) and 0 <= x < 16:
            return getattr(self, f"_{x}")
        else:
            raise KeyError(x)

    # Set available colors to what is configured in the palette
    @staticmethod
    def from_string(palette_line):
        return KBPPalette(*palette_line.lstrip().split(","))

    def as_rgb24(self):
        return ["".join(y * 2 for y in x) for x in self]

    def as_rgba32(self):
        return [x+"FF" for x in self.as_rgb24()]

@validators.validated_instantiation
class KBPLineHeader(typing.NamedTuple):
    align: str
    style: str
    start: int
    end: int
    right: int
    down: int
    rotation: int

    def isfixed(self):
        return self.style.islower()

@validators.validated_instantiation
class KBPSyllable(typing.NamedTuple):
    syllable: str
    start: int
    end: int
    wipe: int

    def isempty(self):
        return self.syllable == ""

class KBPLine(typing.NamedTuple):
    header: KBPLineHeader
    syllables: list

    # There's only one header, so may as well pass anything unresolved down to it
    def __getattr__(self, attr):
        return getattr(self.header, attr)

    # These will just return "can't set" errors since it's immutable, but that
    # makes more sense than "has no attribute"
    def __setattr__(self, attr, val):
        return setattr(self.header, attr, val)

    def text(self, syllable_separator="", space_is_separator=False):

        # Special case for empty lines because when importing lyrics into KBS,
        # a syllable separator by itself is used to denote an empty line (as
        # opposed to a page break)
        if self.isempty():
            return syllable_separator

        # When space is considered a separator, underscore is used to represent
        # a non-splitting space
        elif space_is_separator and syllable_separator != "":
            result = ""
            for syl in self.syllables:
                syltext = syl.syllable
                syltext = re.sub(r"( +)(?=[^ ])", lambda m: "_" * len(m.group(1)), syltext)
                result += syltext
                if not syltext.endswith(" "):
                    result += syllable_separator
            return result[:-len(syllable_separator)]

        # Basic case - output syllables split only by the separator character(s)
        else:
            return syllable_separator.join(x.syllable for x in self.syllables)

    def isempty(self):
        return not self.syllables or (len(self.syllables) == 1 and self.syllables[0].isempty())

@validators.validated_instantiation
class KBPStyle(typing.NamedTuple):
    name: str
    textcolor: str | int
    outlinecolor: str | int
    textwipecolor: str | int
    outlinewipecolor: str | int
    fontname: str
    fontsize: int
    fontstyle: str
    charset: int
    outlines: typing.Iterable
    shadows: typing.Iterable
    wipestyle: int
    allcaps: str
    fixed: bool

    def has_colors(self):
        fields = ("textcolor", "outlinecolor", "textwipecolor", "outlinewipecolor")
        if all(isinstance(getattr(self,x), str) for x in fields):
            return True
        elif all(isinstance(getattr(self,x), int) for x in fields):
            return False
        else:
            raise TypeError("Mixed/unexpected types found in color parameters:\n\t" + 
                "\n\t".join([": ".join((x, str(getattr(self,x)))) for x in fields]))

    def resolve_colors(style, palette):
        if style.has_colors():
            warnings.warn(f"Colors were already resolved on style {name}. Palette may not be used as intended", RuntimeWarning)
            return style
        else:
            fields = ("textcolor", "outlinecolor", "textwipecolor", "outlinewipecolor")
            return style._replace(**dict((x, palette[getattr(style,x)]) for x in fields))

    # Create a "fixed" version of a style (no wiping)
    # Technically the wipe colors are still defined with their original values,
    # but they are not used, so it makes sense to redefine them to their
    # non-wiped values for compatibility with other formats
    def make_fixed(style):
        if style.fixed:
            return style
        else:
            return style._replace(
                name = style.name + "_fixed",
                textwipecolor = style.textcolor,
                outlinewipecolor = style.outlinecolor,
                fixed = True)

class KBPStyleCollection(dict):
    __slots__ = ()

    def __repr__(self):
        return "KBPStyleCollection(" + super().__repr__() + ")"

    # Alias style letters used in line headers to style numbers used in definition
    @staticmethod
    def alpha2key(alpha):
        if alpha in list(string.ascii_uppercase):
            return string.ascii_uppercase.index(alpha)+1
        elif alpha in list(string.ascii_lowercase):
            return -string.ascii_lowercase.index(alpha)-1

    def __missing__(self, key):
        if key in list(string.ascii_letters):
            return self[KBPStyleCollection.alpha2key(key)]
        # Auto-vivify fixed styles
        elif isinstance(key, int) and -key in self.keys():
            self[key] = self[-key].make_fixed()
            return self[key]
        else:
            raise KeyError(key)

    # Hopefully the checks in the next several methods will be enough to keep bad data out...
    @validators.validated_types
    @staticmethod
    def __assert_valid_item(key: int, value: KBPStyle):
        if not (1 <= key <= 26 or -26 <= key <= -1):
            raise KeyError(f"Invalid index for item in KBPStyleCollection: {key}")

    # Using *args is the only way to get unnamed positional arguments.
    # Otherwise if the argument name happens to be a key in the kwargs, it causes
    # a duplicate argument. The decorator validates that it still only gets one
    # argument. As far as I know this is the only way to properly replicate the
    # signatures of the dict methods that can take an object *or* a mapping via
    # kwargs
    @validators.validated_structures(__assert_valid_item)
    @validators.one_arg
    def __init__(self, *args, **kwargs):
        arg = args[0] if args else []
        super().__init__(arg, **kwargs)
    
    def __setitem__(self, key, value):
        KBPStyleCollection.__assert_valid_item(key, value)
        super().__setitem__(key, value)
    
    @validators.validated_structures(__assert_valid_item)
    @validators.one_arg
    def update(self, *args, **kwargs):
        arg = args[0] if args else []
        super().update(arg, **kwargs)

    @validators.validated_structures(__assert_valid_item)
    def __ior__(self, arg):
        return super().__ior__(arg)

    def __or__(self, arg):
        # Data validated by __init__
        return KBPStyleCollection(super().__or__(arg))

    @staticmethod
    def fromkeys(keys, value=None):
        # Data validated by __init__
         return KBPStyleCollection(dict.fromkeys(keys, value))

    # Set available styles based on the configuration
    @staticmethod
    def from_textlines(style_lines, palette=None):
        styles = KBPStyleCollection()
        fields = {}
        style_no = None
        for n, line in enumerate(style_lines):
            line = line.lstrip()
            if line == "" and style_no is not None:
                style_no = None
                fields = {}
            elif style_no is None and line.startswith("Style"):
                tmp = line.split(",")
                style_no = int(tmp[0][5:])
                tmp = [tmp[1], *(int(x) for x in tmp[2:])]
                fields.update(dict(zip(("name", "textcolor", "outlinecolor", "textwipecolor", "outlinewipecolor"),tmp)))
                tmp = style_lines[n+1].lstrip().split(",")
                tmp[1] = int(tmp[1])
                tmp[3] = int(tmp[3])
                fields.update(dict(zip(("fontname", "fontsize", "fontstyle", "charset"),tmp)))
                tmp = style_lines[n+2].lstrip().split(",")
                tmp[:-1] = [int(x) for x in tmp[:-1]]
                fields.update(dict(zip(("outlines", "shadows", "wipestyle", "allcaps", "fixed"),(tmp[:4],tmp[4:6],tmp[6],tmp[7],False))))
                result = KBPStyle(**fields)
                if palette:
                    result = result.resolve_colors(palette)
                # Adding 1 to style for 2 reasons:
                # - Style 0/A is shown in the KBS UI as 01
                # - It allows indexing fixed styles as negative numbers
                styles[style_no+1] = result
            # else second/third line of styles already processed
        return styles
    
    
    def key2alpha(key: int):
        if key < 0:
            return string.ascii_lowercase[-key-1]
        else:
            return string.ascii_uppercase[key-1]

    # This is almost definitely ill-advised, but this is like dict.keys() and
    # returns a view object for the alpha aliases of the keys. And of course it
    # can give you an iterator so you can do something like:
    # for x in kbpfile.styles.alpha_keys():
    #     ...
    def alpha_keys(self):
        class alphakey_view:
            def __init__(slf, obj):
                slf.__obj = obj

            def __len__(slf):
                return len(slf.__obj)

            def __reversed__(slf):
                return alphakey_view(reversed(slf.__obj))

            def __repr__(slf):
                return f"style_alphakey_view({', '.join(repr(KBPStyleCollection.key2alpha(x)) for x in slf.__obj)})"

            def __iter__(slf):
                class alphakey_iterator:
                    def __init__(sf, it):
                        sf.__iter = it
                    def __repr__(sf):
                        return f"<style_alphakey_iterator at {hex(id(sf))}>"
                    def __next__(sf):
                        return next(sf.__iter)
                return alphakey_iterator(KBPStyleCollection.key2alpha(x) for x in slf.__obj)

        return alphakey_view(self.keys())

    def alpha_iter(self):
        return iter(self.alpha_keys())

@validators.validated_instantiation
class KBPPage(typing.NamedTuple):
    remove: str
    display: str
    lines: list

    @staticmethod
    def from_textlines(page_lines, default_wipe = None):
        lines=[]
        syllables=[]
        header=None
        transitions=["", ""] # Default line by line
        for x in page_lines:
            if header is None and re.match(r"[LCR]/[a-zA-Z](/\d+){2}(/-?\d+){3}$", x): # Only last 3 fields can be negative
                fields = x.split("/")
                fields[2:] = [int(y) for y in fields[2:]]
                header = KBPLineHeader(**dict(zip(("align", "style", "start", "end", "right", "down", "rotation"), fields)))
            elif x == "" and header is not None:
                # Handle previous line
                lines.append(KBPLine(header=header, syllables=syllables))
                syllables = []
                header = None
            elif header is None and x.startswith("FX/"):
                transitions = x.split('/')[1:]
            elif x != "":
                fields = x.split("/")
                fields[0] = re.sub(r"{-}", "/", fields[0]) # This field uses this as a surrogate for / since that denotes end of syllable
                fields[1] = fields[1].lstrip() # Only the second field should have extra spaces
                fields[1:] = [int(y) for y in fields[1:]]
                if default_wipe and fields[3] == 0:
                    fields[3] = default_wipe
                syllables.append(KBPSyllable(**dict(zip(("syllable", "start", "end", "wipe"), fields))))
        return KBPPage(*transitions, lines)

    def get_start(self):
        return min(line.start for line in self.lines if not line.isempty())

    def get_end(self):
        return max(line.end for line in self.lines)

# Perhaps this could be better named as slideshow, but calling it IMAGE as that's what the header is called in the .kbp
@validators.validated_instantiation
class KBPImage(typing.NamedTuple):
    start: int
    end: int
    filename: str
    leaveonscreen: int

    @staticmethod
    def from_string(image_line):
        fields = image_line.split("/")
        for x in (0, 1, 3):
            fields[x] = int(fields[x])
        return KBPImage(**dict(zip(("start", "end", "filename", "leaveonscreen"),fields)))
