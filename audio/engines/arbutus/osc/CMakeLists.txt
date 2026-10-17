add_arbutus_engine(osc SOURCES src/engine.cpp)

set(_box "${ARB_BOX_ROOT}/arbutus/osc")
set(_notes "${CMAKE_CURRENT_SOURCE_DIR}/tests/notes.txt")

# The Python conformance for this box is on its *voice*: `ctest -R voice_goldens`
# replays audio/tests/golden/python/osc_*.npy through src/osc_dsp.hpp, bit-exact.
# This golden covers what that one cannot -- allocation, release tails, the
# whole box through the ABI -- and was frozen from this box.
add_test(NAME osc_golden COMMAND arbutus-run "${_box}"
         --notes "${_notes}" --len 48000 --sr 48000 --frames 512 --set wave=2
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/osc.npy" --tol -100)

add_test(NAME ragged_osc
         COMMAND "${ARB_AUDIO_ROOT}/tests/ragged.sh"
                 $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged"
                 --notes "${_notes}" --len 48000 --sr 48000 --set wave=2)

add_test(NAME rt_guard_osc COMMAND arbutus-run "${_box}" --rt-guard
         --notes "${_notes}" --len 48000 --sr 48000)
