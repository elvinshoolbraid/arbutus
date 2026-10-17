add_arbutus_engine(sampler SOURCES src/engine.cpp)

set(_box "${ARB_BOX_ROOT}/arbutus/sampler")
set(_notes "${CMAKE_CURRENT_SOURCE_DIR}/tests/notes.txt")
# 44.1 kHz on purpose: the box resamples on play, so the golden covers the
# rate conversion as well as the pitch.
set(_wav "${ARB_AUDIO_ROOT}/tests/golden/sample.wav")

add_test(NAME sampler_golden COMMAND arbutus-run "${_box}"
         --notes "${_notes}" --len 48000 --sr 48000 --frames 512
         --asset sample=${_wav}
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/sampler.npy" --tol -100)

add_test(NAME ragged_sampler
         COMMAND "${ARB_AUDIO_ROOT}/tests/ragged.sh"
                 $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged"
                 --notes "${_notes}" --len 48000 --sr 48000 --asset sample=${_wav})

add_test(NAME rt_guard_sampler COMMAND arbutus-run "${_box}" --rt-guard
         --notes "${_notes}" --len 48000 --sr 48000 --asset sample=${_wav})

# An engine that declares an asset key must refuse every other one, or the
# host's UNKNOWN ASSET refusal (§7.4) would be the only thing standing between
# a typo and silence.
add_test(NAME sampler_refuses_unknown_asset COMMAND arbutus-run "${_box}"
         --asset kit=${_wav} --len 64)
set_tests_properties(sampler_refuses_unknown_asset PROPERTIES WILL_FAIL TRUE)

# v2: a held note carried by a reversed LOOP-IN..LOOP-OUT with a 30 % seam
# FADE, released into its tail, then a note released inside its first pass.
# Frozen from this box after npystat (peak 0.20, DC -6e-6, finite) and a check
# that the loop carries sound past the sample's own length and that the
# largest step is the sine's, not a seam (0.0079).
set(_notes_loop "${CMAKE_CURRENT_SOURCE_DIR}/tests/notes_loop.txt")
set(_loop_args --set loop_in=0.3 --set loop_out=0.8 --set fade=30 --set rev=1 --env 0.005 0.1 1 0.05)
add_test(NAME sampler_loop_golden COMMAND arbutus-run "${_box}"
         --notes "${_notes_loop}" --len 48000 --sr 48000 --frames 512
         --asset sample=${_wav} ${_loop_args}
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/sampler_loop.npy" --tol -100)
add_test(NAME ragged_sampler_loop
         COMMAND "${ARB_AUDIO_ROOT}/tests/ragged.sh"
                 $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged_loop"
                 --notes "${_notes_loop}" --len 48000 --sr 48000 --asset sample=${_wav} ${_loop_args})
