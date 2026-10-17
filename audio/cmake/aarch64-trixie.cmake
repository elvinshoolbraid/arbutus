# Cross-build audio/ for a 64-bit Raspberry Pi (linux-aarch64) from x86 — #568.
#
# Used inside the Debian trixie chroot that tools/cross-build.sh makes, never
# on its own: the compilers below are Debian's crossbuild-essential-arm64
# (GCC 14, the same release the Pi's trixie runs), and qemu-user runs the one
# target binary the build itself executes (arb-manifest, which dlopen()s each
# aarch64 box to write its engine.json).
#
# No ISA flags, as for a native build (audio/CLAUDE.md § Goldens).
set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)   # ARB_ARCH = linux-aarch64, as on the Pi

set(CMAKE_C_COMPILER   aarch64-linux-gnu-gcc)
set(CMAKE_CXX_COMPILER aarch64-linux-gnu-g++)

set(CMAKE_FIND_ROOT_PATH /usr/aarch64-linux-gnu)
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)   # cmake, python3: the build machine's
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)

# Not CMAKE_CROSSCOMPILING_EMULATOR: that goes on every executable, and CTest
# then starts each test through qemu-aarch64, which the Pi does not have (119
# tests "Not Run" on the first try). sdk/CMakeLists.txt puts it on
# arb-manifest, the one target the build runs.
set(ARB_CROSS_EMULATOR qemu-aarch64 -L /usr/aarch64-linux-gnu)
