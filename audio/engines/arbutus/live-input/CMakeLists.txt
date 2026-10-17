add_arbutus_engine(live-input SOURCES src/engine.cpp)

set(_box "${ARB_BOX_ROOT}/arbutus/live-input")
# The box has no audio_in: the input arrives on side[], so the "input" file for
# every test below is the sidechain one.
set(_side "${ARB_AUDIO_ROOT}/tests/golden/fx_side.npy")

add_test(NAME live_input_golden COMMAND arbutus-run "${_box}"
         --side "${_side}" --sr 48000 --frames 512
         --set source=3 --set gain=6
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/live-input.npy" --tol -100)

add_test(NAME ragged_live_input
         COMMAND "${ARB_AUDIO_ROOT}/tests/ragged.sh"
                 $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged"
                 --side "${_side}" --sr 48000 --set gain=6)

add_test(NAME rt_guard_live_input COMMAND arbutus-run "${_box}" --rt-guard
         --side "${_side}" --sr 48000)
