# The template box. Copy this folder, rename the target, keep the shape.
# The folder is <author>/<target>: the id in arb_describe() must say the same
# (arb-manifest refuses anything else, #675).
add_arbutus_engine(example SOURCES src/engine.cpp)

set(_box "${ARB_BOX_ROOT}/arbutus/example")
set(_notes "${CMAKE_CURRENT_SOURCE_DIR}/tests/notes.txt")

# `ctest -R example` is the template's "make test": the three a box is done
# with (README § Build) and the manifest check.
add_test(NAME example_golden COMMAND arbutus-run "${_box}"
         --notes "${_notes}" --frames 512 --sr 48000 --len 32768
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/example.npy" --tol -100)

# Rule 8: the same samples at every block size.
add_test(NAME ragged_example COMMAND "${ARB_AUDIO_ROOT}/tests/ragged.sh"
         $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged"
         --notes "${_notes}" --len 32768 --sr 48000)

# Rule 1: nothing allocated inside process().
add_test(NAME rt_guard_example COMMAND arbutus-run "${_box}" --rt-guard
         --notes "${_notes}" --len 32768 --sr 48000)

add_test(NAME example_manifest COMMAND arbutus-run "${_box}" --describe)
