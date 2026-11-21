"""App entry point. `python3 -m wordstudy`"""
import sys


def main():
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import Gtk  # noqa: F401
    except (ImportError, ValueError) as error:
        print("GTK 3/PyGObject is required. See README.md for installation:\n" + str(error), file=sys.stderr)
        return 2
    from .app import WordStudyApp
    return WordStudyApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
