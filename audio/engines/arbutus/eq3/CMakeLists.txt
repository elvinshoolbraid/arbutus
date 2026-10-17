add_arbutus_engine(eq3 SOURCES src/engine.cpp)

set(_box "${ARB_BOX_ROOT}/arbutus/eq3")
set(_in "${ARB_AUDIO_ROOT}/tests/golden/fx_input.npy")

# Frozen from this box (the emulator's only EQ was the one-pole in
# arbutus/biquad). All three bands off zero with both shelves moved and a
# narrow mid, so every section is in the render. Checked with
# tests/tools/npystat.py; README § Tests.
add_test(NAME eq3_golden COMMAND arbutus-run "${_box}"
         --in "${_in}" --sr 48000 --frames 512
         --set low=6.5 --set mid=-9 --set high=4 --set mid_f=1400
         --set low_f=120 --set high_f=5000 --set mid_q=2.5 --set out=-2
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/eq3.npy" --tol -100)

add_test(NAME ragged_eq3
         COMMAND "${ARB_AUDIO_ROOT}/tests/ragged.sh"
                 $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged"
                 --in "${_in}" --sr 48000
                 --set low=6.5 --set mid=-9 --set high=4 --set mid_f=1400
                 --set low_f=120 --set high_f=5000 --set mid_q=2.5 --set out=-2)

# #630 row 6: all three bands at 0 dB (and OUT 0) is the input, bit for bit, wherever
# the corners and Q sit (README § How it is built).
add_test(NAME eq3_bypass COMMAND arbutus-run "${_box}"
         --in "${_in}" --sr 48000 --frames 512 --set low=0 --set mid=0 --set high=0 --set out=0 --set mid_f=100 --set low_f=600 --set high_f=12000 --set mid_q=6
         --golden "${_in}" --tol -999)

add_test(NAME rt_guard_eq3 COMMAND arbutus-run "${_box}" --rt-guard
         --in "${_in}" --sr 48000
         --set low=18 --set mid=18 --set high=18 --set mid_q=6 --set out=12)
