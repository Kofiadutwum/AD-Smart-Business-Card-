"""Keep the translation catalogs in step with the text in the code.

GNU gettext (xgettext, msgfmt) is not needed: sentences are read from the Python files and the
templates here, and the .mo files are compiled with polib. polib is a development tool; the
compiled catalogs are committed, so the live site needs neither.

    python manage.py translations extract    # find every sentence, update locale/<language>/LC_MESSAGES/django.po
    python manage.py translations compile    # check each translation, then write django.mo for every language
    python manage.py translations status     # how much of each language is translated

Anything not yet translated shows in English, so a half-finished catalog never breaks a page.
"""

import ast
import re
from collections import Counter, OrderedDict
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import to_locale

# Calls whose first argument is a sentence to translate (and, for the plural ones, the second too).
SINGULAR_CALLS = {"_", "gettext", "gettext_lazy", "subject_text"}
PLURAL_CALLS = {"ngettext", "ngettext_lazy", "subject_plural"}

# Python's plural rule for each language (the catalog header); "English" needs none.
PLURAL_FORMS = {
    "fr": "nplurals=2; plural=(n > 1);",
    "zh-hans": "nplurals=1; plural=0;",
    "es": "nplurals=2; plural=(n != 1);",
    "nl": "nplurals=2; plural=(n != 1);",
    "pt": "nplurals=2; plural=(n != 1);",
}

STRING = r"""(?:'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")"""
TEMPLATE_CALL = re.compile(r"\b(n?gettext|_)\(\s*u?(?P<a>%s)(?:\s*,\s*u?(?P<b>%s))?" % (STRING, STRING))
CONTEXT_CALL = re.compile(r"\b(?:n?pgettext)\(")
PLACEHOLDER = re.compile(r"%\((\w+)\)[sd]")
TAG = re.compile(r"</?\s*([a-zA-Z][a-zA-Z0-9]*)")

# The helpers that do the translating take the sentence as a variable; that is not a missed sentence.
HELPER_FILES = {"apps/core/emails.py", "apps/core/templatetags/ui.py"}

SKIP_DIRS = {"migrations", "__pycache__", "node_modules", ".venv", "graphify-out", "claude-flow"}


def _literal(node):
    """The text of a string constant, or of several joined with +; None when it is not fixed text."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal(node.left), _literal(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _files(root, suffixes):
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in suffixes and not (SKIP_DIRS & set(path.parts)):
            yield path


class Command(BaseCommand):
    help = "Extract, check, compile and report the translation catalogs (no GNU gettext needed)."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["extract", "compile", "status"])

    def handle(self, *args, action, **options):
        try:
            import polib  # noqa: F401
        except ImportError:
            raise CommandError("polib is needed for this command: pip install polib (development only).")
        self.base = Path(settings.BASE_DIR)
        self.locale_root = self.base / "locale"
        self.languages = [(code, name) for code, name in settings.LANGUAGES if code != settings.LANGUAGE_CODE]
        getattr(self, action)()

    # ------------------------------------------------------------------ finding the sentences
    def collect(self):
        """Every sentence in the code: {(msgid, plural): [file:line, ...]}, plus any call that was not fixed text."""
        found = OrderedDict()
        dynamic = []

        def add(msgid, plural, place):
            found.setdefault((msgid, plural), []).append(place)

        for path in _files(self.base / "apps", {".py"}):
            rel = path.relative_to(self.base).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in sorted((n for n in ast.walk(tree) if isinstance(n, ast.Call)), key=lambda n: n.lineno):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
                if name in SINGULAR_CALLS and node.args:
                    text = _literal(node.args[0])
                    if text is None:
                        if rel not in HELPER_FILES and not isinstance(node.args[0], ast.Attribute):
                            dynamic.append(f"{rel}:{node.lineno}")
                    else:
                        add(text, None, f"{rel}:{node.lineno}")
                elif name in PLURAL_CALLS and len(node.args) >= 2:
                    one, many = _literal(node.args[0]), _literal(node.args[1])
                    if one is None or many is None:
                        if rel not in HELPER_FILES:
                            dynamic.append(f"{rel}:{node.lineno}")
                    else:
                        add(one, many, f"{rel}:{node.lineno}")

        from django.utils.translation.template import templatize

        for path in _files(self.base / "templates", {".html", ".txt"}):
            rel = path.relative_to(self.base).as_posix()
            source = path.read_text(encoding="utf-8")
            code = templatize(source, origin=rel)
            if CONTEXT_CALL.search(code):
                raise CommandError(f"{rel} uses a translation context; this command does not read those yet.")
            for match in TEMPLATE_CALL.finditer(code):
                line = code.count("\n", 0, match.start()) + 1
                one = ast.literal_eval(match.group("a"))
                many = ast.literal_eval(match.group("b")) if match.group("b") and match.group(1) == "ngettext" else None
                add(one, many, f"{rel}:{line}")
        return found, dynamic

    # ------------------------------------------------------------------ the catalogs
    def po_path(self, code):
        return self.locale_root / to_locale(code) / "LC_MESSAGES" / "django.po"

    def new_catalog(self, code=None):
        import polib

        po = polib.POFile(wrapwidth=0)
        po.metadata = OrderedDict([
            ("Project-Id-Version", "AD Smart Business Cards"),
            ("Report-Msgid-Bugs-To", ""),
            ("MIME-Version", "1.0"),
            ("Content-Type", "text/plain; charset=UTF-8"),
            ("Content-Transfer-Encoding", "8bit"),
        ])
        if code:
            po.metadata["Language"] = to_locale(code)
            po.metadata["Plural-Forms"] = PLURAL_FORMS[code]
        return po

    def extract(self):
        import polib

        found, dynamic = self.collect()
        self.locale_root.mkdir(exist_ok=True)

        pot = self.new_catalog()
        for (msgid, plural), places in found.items():
            entry = polib.POEntry(msgid=msgid, msgid_plural=plural or "", occurrences=self._places(places))
            if PLACEHOLDER.search(msgid):
                entry.flags.append("python-format")
            pot.append(entry)
        pot.save(str(self.locale_root / "django.pot"))

        for code, _name in self.languages:
            path = self.po_path(code)
            path.parent.mkdir(parents=True, exist_ok=True)
            po = polib.pofile(str(path), wrapwidth=0) if path.exists() else self.new_catalog(code)
            forms = int(re.search(r"nplurals=(\d+)", PLURAL_FORMS[code]).group(1))
            current = set()
            added = 0
            for (msgid, plural), places in found.items():
                entry = po.find(msgid)
                if entry is None:
                    entry = polib.POEntry(msgid=msgid)
                    po.append(entry)
                    added += 1
                entry.obsolete = False
                entry.msgid_plural = plural or ""
                entry.occurrences = self._places(places)
                if plural and not entry.msgstr_plural:
                    entry.msgstr_plural = {i: "" for i in range(forms)}
                if PLACEHOLDER.search(msgid) and "python-format" not in entry.flags:
                    entry.flags.append("python-format")
                current.add(msgid)
            gone = 0
            for entry in po:
                if entry.msgid not in current and not entry.obsolete:
                    entry.obsolete = True
                    gone += 1
            po.metadata["Language"] = to_locale(code)
            po.metadata["Plural-Forms"] = PLURAL_FORMS[code]
            po.save(str(path))
            self.stdout.write(f"{code}: {added} new, {gone} no longer used, {len(po.untranslated_entries())} to translate")
        self.stdout.write(self.style.SUCCESS(f"{len(found)} sentences found."))
        for place in dynamic:
            self.stdout.write(self.style.WARNING(f"Not fixed text, so not extracted: {place}"))

    @staticmethod
    def _places(places):
        out = []
        for place in places:
            file, line = place.rsplit(":", 1)
            out.append((file, line))
        return out

    # ------------------------------------------------------------------ checking and compiling
    @staticmethod
    def _problems(entry):
        """Differences between a sentence and its translation that would break a page."""
        pairs = [(entry.msgid, entry.msgstr)] if not entry.msgid_plural else [
            (entry.msgid if i == 0 else entry.msgid_plural, text) for i, text in sorted(entry.msgstr_plural.items())
        ]
        problems = []
        for source, translated in pairs:
            if not translated:
                continue
            want, got = Counter(PLACEHOLDER.findall(source)), Counter(PLACEHOLDER.findall(translated))
            # A plural form for 1 may leave out the number; the others must keep the same names.
            if want != got and not (entry.msgid_plural and set(got) <= set(want)):
                problems.append(f"placeholders differ: {sorted(want.elements())} -> {sorted(got.elements())}")
            if Counter(TAG.findall(source)) != Counter(TAG.findall(translated)):
                problems.append("HTML tags differ")
            # A literal percent sign is written %% in these sentences; it must stay that way.
            lone = r"%(?!\()"
            if source.count("%%") != translated.count("%%") or len(re.findall(lone, source.replace("%%", ""))) != len(
                re.findall(lone, translated.replace("%%", ""))
            ):
                problems.append("percent signs differ")
        return problems

    def compile(self):
        import polib

        failed = 0
        for code, _name in self.languages:
            path = self.po_path(code)
            if not path.exists():
                self.stdout.write(self.style.WARNING(f"{code}: no catalog yet (run extract)"))
                continue
            po = polib.pofile(str(path), wrapwidth=0)
            bad = 0
            for entry in po.translated_entries():
                for problem in self._problems(entry):
                    bad += 1
                    self.stderr.write(f"{code}: {problem}: {entry.msgid[:70]!r}")
            failed += bad
            if bad:
                continue
            po.save_as_mofile(str(path.with_suffix(".mo")))
            done = len(po.translated_entries())
            self.stdout.write(f"{code}: {done} of {done + len(po.untranslated_entries())} translated -> django.mo")
        if failed:
            raise CommandError(f"{failed} translation(s) would break a page. Fix them and compile again.")

    def status(self):
        import polib

        for code, _name in self.languages:
            path = self.po_path(code)
            if not path.exists():
                self.stdout.write(f"{code}: no catalog")
                continue
            po = polib.pofile(str(path), wrapwidth=0)
            done, todo = len(po.translated_entries()), len(po.untranslated_entries())
            self.stdout.write(f"{code}: {done} translated, {todo} still in English ({100 * done // max(done + todo, 1)}%)")
