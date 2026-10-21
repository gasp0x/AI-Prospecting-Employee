"""Empacotamento do app desktop MAA (py2app — macOS).

Modo alias (padrão deste projeto): o bundle referencia os arquivos do
repositório/venv — rápido e sempre atualizado com o código. Para gerar:
    .venv/bin/python setup.py py2app
"""
from setuptools import setup

setup(
    app=["wilson_desktop.py"],
    data_files=[],
    options={
        "py2app": {
            "argv_emulation": False,
            "alias": True,
            "iconfile": "assets/app.icns",
            "plist": {
                "CFBundleName": "Wilson",
                "CFBundleDisplayName": "Wilson",
                "CFBundleIdentifier": "br.arthur.wilson",
                "CFBundleShortVersionString": "0.2.0",
            },
            "excludes": ["tkinter"],
        }
    },
    setup_requires=["py2app"],
)
