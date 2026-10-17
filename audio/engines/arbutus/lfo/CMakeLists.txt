add_arbutus_engine(lfo SOURCES src/engine.cpp)

set(_box "${ARB_BOX_ROOT}/arbutus/lfo")
set(_notes "${CMAKE_CURRENT_SOURCE_DIR}/tests/notes.txt")

# A modulator's golden is its control outputs: arbutus-run writes one channel
# per output, each block's value held across the block's frames. Frozen from
# this box (no Python LFO to replay); see README.md for what was checked.
# 1/8 at 120 BPM, NOTE retrigger, fade, SYM, PHASE and MORPH all off centre.
add_test(NAME lfo_golden COMMAND arbutus-run "${_box}"
         --notes "${_notes}" --len 96000 --sr 48000 --frames 512 --tempo 120 --seed 7
         --set rate=0.6231 --set morph=0.3 --set phase=90 --set retrig=1
         --set fade_in=0.25 --set sym=30
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/lfo_sync.npy" --tol -100)
# Free Hz at the device block, no notes, defaults otherwise.
add_test(NAME lfo_golden_free COMMAND arbutus-run "${_box}"
         --len 96000 --sr 48000 --frames 128 --seed 3 --set rate=-0.5
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/lfo_free.npy" --tol -100)

add_test(NAME rt_guard_lfo COMMAND arbutus-run "${_box}" --rt-guard
         --notes "${_notes}" --len 96000 --sr 48000 --set retrig=1 --set morph=0.5)

add_executable(test_lfo_format tests/test_format.cpp)
target_link_libraries(test_lfo_format PRIVATE arb_sdk arb_hostlib)
add_dependencies(test_lfo_format lfo_box)
add_test(NAME lfo_format COMMAND test_lfo_format "${_box}")
