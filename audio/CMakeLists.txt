# audio/ — the C++20 audio side of Arbutus-1 (engine-spec.md §8, §9).
# Stage 1: SDK, template engine, stock boxes, the arbutus-run test client.
# Stage 2: arbutus-audio, the audio process -- pipeline, rack, tape, protocol,
# live audio through miniaudio and an --offline mode that needs no device.
cmake_minimum_required(VERSION 3.20)
project(arbutus_audio LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE RelWithDebInfo CACHE STRING "" FORCE)
endif()

add_compile_options(-Wall -Wextra)

set(ARB_AUDIO_ROOT "${CMAKE_CURRENT_SOURCE_DIR}" CACHE INTERNAL "")
set(ARB_BOX_ROOT "${CMAKE_BINARY_DIR}/boxes" CACHE INTERNAL "")

enable_testing()

add_subdirectory(sdk)
add_subdirectory(hostlib)
add_subdirectory(host)
add_subdirectory(tools/arbutus-run)
# Stage 3: the stock engines, one box each (§9.1, §11 step 3).
add_subdirectory(engines/arbutus/biquad)
add_subdirectory(engines/arbutus/comb)
add_subdirectory(engines/arbutus/satcomb)
add_subdirectory(engines/arbutus/reverb)
add_subdirectory(engines/arbutus/plate)
add_subdirectory(engines/arbutus/duck)
add_subdirectory(engines/arbutus/transient)
add_subdirectory(engines/arbutus/osc)
add_subdirectory(engines/arbutus/fm)
add_subdirectory(engines/arbutus/noise)
add_subdirectory(engines/arbutus/sampler)
add_subdirectory(engines/arbutus/drum)
add_subdirectory(engines/arbutus/endless)
# Stage H1: the other three sequencers (engine-spec §15.4).
add_subdirectory(engines/arbutus/arp)
add_subdirectory(engines/arbutus/euclid)
add_subdirectory(engines/arbutus/pattern)
# Stage G: the live input as an engine (DDR-063).
add_subdirectory(engines/arbutus/live-input)
# Stage I2: the effect boxes (v1-spec-package § Scope).
add_subdirectory(engines/arbutus/delay)
add_subdirectory(engines/arbutus/chorus)
add_subdirectory(engines/arbutus/crusher)
add_subdirectory(engines/arbutus/drive)
add_subdirectory(engines/arbutus/eq3)
# Stage I1: the synth boxes (v1-spec-package § Scope).
add_subdirectory(engines/arbutus/va)
add_subdirectory(engines/arbutus/string)
add_subdirectory(engines/arbutus/wavetable)
add_subdirectory(engines/arbutus/dbox)
# MOD rack modulators (modulation-spec §3.2).
add_subdirectory(engines/arbutus/lfo)
add_subdirectory(engines/arbutus/follower)
add_subdirectory(engines/arbutus/random)
add_subdirectory(engines/arbutus/envelope)
add_subdirectory(engines/arbutus/motion)
add_subdirectory(engines/arbutus/stepmod)
# The OP-1 adapter boxes: op1r's effects, unmodified (#1230).
add_subdirectory(engines/op1r)
add_subdirectory(tests)
