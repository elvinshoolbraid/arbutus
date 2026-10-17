add_arbutus_engine(chorus SOURCES src/engine.cpp)

set(_box "${ARB_BOX_ROOT}/arbutus/chorus")
set(_in "${ARB_AUDIO_ROOT}/tests/golden/fx_input.npy")

# Frozen from this box (the emulator had no chorus), at a flanger-ish setting
# that exercises every path: three voices, a wide stereo spread, negative
# feedback and the wet-only tilt. Checked with tests/tools/npystat.py before
# freezing; README § Tests.
add_test(NAME chorus_golden COMMAND arbutus-run "${_box}"
         --in "${_in}" --len 30000 --sr 48000 --frames 512
         --set rate=0.8 --set depth=0.7 --set delay=3.5 --set mix=0.6
         --set feedback=-0.55 --set spread=150 --set voices=3 --set tone=0.35
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/chorus.npy" --tol -100)

add_test(NAME ragged_chorus
         COMMAND "${ARB_AUDIO_ROOT}/tests/ragged.sh"
                 $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged"
                 --in "${_in}" --len 30000 --sr 48000
                 --set rate=0.8 --set depth=0.7 --set delay=3.5 --set mix=0.6
                 --set feedback=-0.55 --set spread=150 --set voices=3 --set tone=0.35)

# #630 row 6: MIX 0 is the input, bit for bit (README § mix).
add_test(NAME chorus_bypass COMMAND arbutus-run "${_box}"
         --in "${_in}" --sr 48000 --frames 512 --set mix=0 --set depth=1 --set feedback=0.9 --set voices=3 --set tone=0
         --golden "${_in}" --tol -999)

add_test(NAME rt_guard_chorus COMMAND arbutus-run "${_box}" --rt-guard
         --in "${_in}" --len 30000 --sr 48000
         --set rate=8 --set depth=1 --set delay=30 --set voices=3 --set feedback=0.9)
