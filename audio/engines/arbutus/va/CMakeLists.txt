add_arbutus_engine(va SOURCES src/engine.cpp)

set(_box "${ARB_BOX_ROOT}/arbutus/va")
set(_notes "${CMAKE_CURRENT_SOURCE_DIR}/tests/notes.txt")

# Frozen from this box at its defaults (there is no Python original to replay:
# the emulator never had a filter with a per-voice envelope on it). Checked
# with tests/tools/npystat.py before freezing -- see README § Tests.
add_test(NAME va_golden COMMAND arbutus-run "${_box}"
         --notes "${_notes}" --len 48000 --sr 48000 --frames 512
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/va.npy" --tol -100)

add_test(NAME ragged_va
         COMMAND "${ARB_AUDIO_ROOT}/tests/ragged.sh"
                 $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged"
                 --notes "${_notes}" --len 48000 --sr 48000)

add_test(NAME rt_guard_va COMMAND arbutus-run "${_box}" --rt-guard
         --notes "${_notes}" --len 48000 --sr 48000)
