add_arbutus_engine(delay SOURCES src/engine.cpp)

set(_box "${ARB_BOX_ROOT}/arbutus/delay")
set(_in "${ARB_AUDIO_ROOT}/tests/golden/fx_input.npy")

# Frozen from this box (there is no Python original: the emulator had no
# delay), at a setting that exercises every path at once -- a synced TIME, a
# filtered and saturated loop, ping-pong, wow and ducking. `--tempo 96` so the
# golden also pins that the delay time comes from the *host*, not from a
# constant. Checked with tests/tools/npystat.py before freezing; README § Tests.
add_test(NAME delay_golden COMMAND arbutus-run "${_box}"
         --in "${_in}" --len 36000 --sr 48000 --frames 512 --tempo 96
         --set time=0.5 --set feedback=0.6 --set tone=0.28 --set mix=0.5
         --set ping=0.7 --set wow=0.4 --set drive=0.35 --set duck=0.5
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/delay.npy" --tol -100)

add_test(NAME ragged_delay
         COMMAND "${ARB_AUDIO_ROOT}/tests/ragged.sh"
                 $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged"
                 --in "${_in}" --len 36000 --sr 48000 --tempo 96
                 --set time=0.5 --set feedback=0.6 --set tone=0.28 --set mix=0.5
                 --set ping=0.7 --set wow=0.4 --set drive=0.35 --set duck=0.5)

# #630 row 6: MIX 0 is the input, bit for bit, whatever the loop is doing.
add_test(NAME delay_bypass COMMAND arbutus-run "${_box}"
         --in "${_in}" --sr 48000 --frames 512 --set mix=0 --set feedback=0.95 --set drive=1 --set ping=1 --set wow=1 --set tone=0.1 --set duck=1
         --golden "${_in}" --tol -999)

add_test(NAME rt_guard_delay COMMAND arbutus-run "${_box}" --rt-guard
         --in "${_in}" --len 36000 --sr 48000 --tempo 96
         --set time=-0.6 --set feedback=0.9 --set drive=0.8 --set duck=1.0)
