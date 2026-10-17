add_arbutus_engine(crusher SOURCES src/engine.cpp)

set(_box "${ARB_BOX_ROOT}/arbutus/crusher")
set(_in "${ARB_AUDIO_ROOT}/tests/golden/fx_input.npy")

# Frozen from this box (the emulator had no crusher). JITTER is on, so the
# golden also pins that the clock's randomness is seeded from host.rng_seed and
# advances once per hold -- not once per sample, which would make it depend on
# the block size. Checked with tests/tools/npystat.py; README § Tests.
add_test(NAME crusher_golden COMMAND arbutus-run "${_box}"
         --in "${_in}" --sr 48000 --frames 512 --seed 20260918
         --set bits=6.5 --set rate=5500 --set drive=9 --set mix=0.8
         --set tone=4200 --set jitter=0.45
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/crusher.npy" --tol -100)

add_test(NAME ragged_crusher
         COMMAND "${ARB_AUDIO_ROOT}/tests/ragged.sh"
                 $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged"
                 --in "${_in}" --sr 48000 --seed 20260918
                 --set bits=6.5 --set rate=5500 --set drive=9 --set mix=0.8
                 --set tone=4200 --set jitter=0.45)

# #630 row 6: MIX 0 is the input, bit for bit (README § mix).
add_test(NAME crusher_bypass COMMAND arbutus-run "${_box}"
         --in "${_in}" --sr 48000 --frames 512 --set mix=0 --set bits=2 --set rate=200 --set drive=24 --set jitter=1 --set tone=500
         --golden "${_in}" --tol -999)

add_test(NAME rt_guard_crusher COMMAND arbutus-run "${_box}" --rt-guard
         --in "${_in}" --sr 48000
         --set bits=2 --set rate=200 --set drive=24 --set jitter=1 --set tone=500)
