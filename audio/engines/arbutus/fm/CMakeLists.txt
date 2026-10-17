add_arbutus_engine(fm SOURCES src/engine.cpp)

set(_box "${ARB_BOX_ROOT}/arbutus/fm")
set(_notes "${CMAKE_CURRENT_SOURCE_DIR}/tests/notes.txt")

# audio/tests/golden/python/fm_voice.npy is replayed through src/fm_dsp.hpp by
# `ctest -R voice_goldens` at the raw ratio 2.5 / index 3.0 the Python used --
# values this box's re-ranged descriptor deliberately cannot reach (DDR-044).
# This golden is the box as it actually ships, at its defaults.
add_test(NAME fm_golden COMMAND arbutus-run "${_box}"
         --notes "${_notes}" --len 48000 --sr 48000 --frames 512
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/fm.npy" --tol -100)

add_test(NAME ragged_fm
         COMMAND "${ARB_AUDIO_ROOT}/tests/ragged.sh"
                 $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged"
                 --notes "${_notes}" --len 48000 --sr 48000)

add_test(NAME rt_guard_fm COMMAND arbutus-run "${_box}" --rt-guard
         --notes "${_notes}" --len 48000 --sr 48000)
