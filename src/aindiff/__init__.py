"""aindiff - side-by-side text editor for AliceSoft .ain scenario files.

The heavy lifting (parsing/writing the .ain container) is delegated to the
official `alice-tools` project (https://github.com/nunuhara/alice-tools).
This package provides the comparison model and GUI/CLI on top of it.
"""

__version__ = "0.5.0"

APP_NAME = "AinDiff 双栏文本编辑"
ALICE_TOOLS_URL = "https://github.com/nunuhara/alice-tools"
ALICE_RELEASE_URL = "https://github.com/nunuhara/alice-tools/releases/download/0.13.0/alice-tools-0.13.0.zip"
ALICE_RELEASE_TAG = "0.13.0"
# SHA-256 of alice.exe in the supported Windows release.
ALICE_EXE_SHA256 = "4123746e53a6b51eaae06c18ce145ecc8f866a0fc376541d8e9e529aa805a6de"
