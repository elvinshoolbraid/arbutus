add_arbutus_engine(noise SOURCES src/engine.cpp)

set(_box "${ARB_BOX_ROOT}/arbutus/noise")
set(_notes "${CMAKE_CURRENT_SOURCE_DIR}/tests/notes.txt")

# --seed is arb_host::rng_seed (§3.3), and this box is the reason that field
# exists: its voices seed numpy's own MT19937 stream from it, which is what
# makes audio/tests/golden/python/noise_voice.npy -- a golden made of noise -- a
# conformance test (`ctest -R voice_goldens`).
add_test(NAME noise_golden COMMAND arbutus-run "${_box}"
         --notes "${_notes}" --len 48000 --sr 48000 --frames 512 --seed 1234 --set type=1
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/noise.npy" --tol -100)

# Block size must not be audible even here: one RNG stream per voice, advancing
# once per frame that voice sounds. A stream shared across voices would hand
# out its draws in whatever order the host's block boundaries produced.
add_test(NAME ragged_noise
         COMMAND "${ARB_AUDIO_ROOT}/tests/ragged.sh"
                 $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged"
                 --notes "${_notes}" --len 48000 --sr 48000 --seed 1234 --set type=1)

add_test(NAME rt_guard_noise COMMAND arbutus-run "${_box}" --rt-guard
         --notes "${_notes}" --len 48000 --sr 48000 --seed 1234)
