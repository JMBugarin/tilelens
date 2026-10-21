[app]
title = TileLens
project_dir = .
input_file = tilelens_main.py
exec_directory = .
project_file =
icon =

[python]
packages = Nuitka==4.1.1

[qt]
qml_files =
excluded_qml_plugins =
modules =
plugins =

[nuitka]
macos.permissions =
mode = standalone
extra_args = --quiet --noinclude-qt-translations --include-data-dir=../profiles=profiles
