add_arbutus_engine(string SOURCES src/engine.cpp)

set(_box "${ARB_BOX_ROOT}/arbutus/string")
set(_notes "${CMAKE_CURRENT_SOURCE_DIR}/tests/notes.txt")

# Frozen from this box at its defaults -- there is no Python original; the
# emulator had no physical model. Checked with tests/tools/npystat.py and by
# measuring the ring time against DECAY before freezing (README § Tests).
add_test(NAME string_golden COMMAND arbutus-run "${_box}"
         --notes "${_notes}" --len 48000 --sr 48000 --frames 512
         --env 0.002 0.05 1.0 0.6
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/string.npy" --tol -100)

add_test(NAME ragged_string
         COMMAND "${ARB_AUDIO_ROOT}/tests/ragged.sh"
                 $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged"
                 --notes "${_notes}" --len 48000 --sr 48000 --env 0.002 0.05 1.0 0.6)

add_test(NAME rt_guard_string COMMAND arbutus-run "${_box}" --rt-guard
         --notes "${_notes}" --len 48000 --sr 48000 --env 0.002 0.05 1.0 0.6)

# BOW is the mode that feeds the loop every sample, so it gets its own
# allocation check: nothing in it may allocate either.
add_test(NAME rt_guard_string_bow COMMAND arbutus-run "${_box}" --rt-guard
         --notes "${_notes}" --len 48000 --sr 48000 --set exc=1)
