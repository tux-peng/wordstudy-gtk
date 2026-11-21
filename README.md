# WordStudy GTK
Native Linux Bible study application built with GTK 3 and Python. Reads unencrypted MySword and MyBible SQLite modules; it is not a port of theWord, MySword, or MyBible.

## Features

- Read chapters with book navigation, typed references, and optional parallel translations.
- Browse commentaries, dictionaries (including Strong’s), private notes, and bookmarks.
- Open study books and journals in the large center window, including MySword map modules (images are read from the module’s `data` table).
- Display words of Christ in red when marked by a module: MySword `<FR>…<Fr>` or HTML color, or MyBible `<J>…</J>`.
- Toggle Strong’s and morphology displays; adjust fonts and wrapping in Preferences.
- Import `.mybible`, `.SQLite3`, `.gz`, and `.zip` files.

## Run

Install the dependencies for your distribution, then run `./run.sh` from this directory.

**Debian/Ubuntu**

```sh
sudo apt install python3 python3-gi gir1.2-gtk-3.0
./run.sh
```

**Fedora**

```sh
sudo dnf install python3 python3-gobject gtk3
./run.sh
```

**Arch / Manjaro**

```sh
sudo pacman -S python python-gobject gtk3
./run.sh
```

Install a user-local launcher with `./install-user.sh`.

Install sample modules with:

```sh
python3 -m wordstudy.sample --install
```

## Where to Get Modules

- Bibles: <https://www.ph4.org/b4_index.php>
- Bibles, commentaries, dictionaries, books, and map collections: <https://www.mysword.info/>

## Use

Import a Bible (`.bbl.mybible` or `.SQLite3`), then import commentaries, dictionaries, books, or map collections from **Import Modules**. Choose a book topic on the right to open it in the center reader. Select **Bible reader** to return to the chapter view.

Modules are stored in `~/.local/share/wordstudy-gtk/modules/`. Notes are stored in `personal.sqlite3`.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

## License

App code is licensed under the [Blue Oak Model License 1.0.0](https://blueoakcouncil.org/license/1.0.0) (`LICENSE`). You are responsible for the terms of any Bible modules you import. Demo excerpts are illustrative only.es, dictionaries, books, and map collections: <https://www.mysword.info/>