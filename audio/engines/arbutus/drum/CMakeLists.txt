add_arbutus_engine(drum SOURCES src/engine.cpp)
# The kit is a §7.3 JSON document, so this one box reads the vendored
# single-header JSON library. It is compiled in, not linked, so the box still
# ships as one file with no runtime dependency.
target_link_libraries(drum PRIVATE arb_json)

set(_box "${ARB_BOX_ROOT}/arbutus/drum")
set(_notes "${CMAKE_CURRENT_SOURCE_DIR}/tests/notes.txt")
set(_kit "${ARB_AUDIO_ROOT}/tests/golden/kit/kit.json")

add_test(NAME drum_golden COMMAND arbutus-run "${_box}"
         --notes "${_notes}" --len 48000 --sr 48000 --frames 512
         --asset kit=${_kit} --env 0 4 0 0
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/drum.npy" --tol -100)

add_test(NAME ragged_drum
         COMMAND "${ARB_AUDIO_ROOT}/tests/ragged.sh"
                 $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged"
                 --notes "${_notes}" --len 48000 --sr 48000 --asset kit=${_kit} --env 0 4 0 0)

add_test(NAME rt_guard_drum COMMAND arbutus-run "${_box}" --rt-guard
         --notes "${_notes}" --len 48000 --sr 48000 --asset kit=${_kit} --env 0 4 0 0)

# v2 (§15.3): per-pad params read from a v2-shape kit, an ENV AD with a real
# attack, a gated loop, REV, pan and a choke group. Frozen from this box after
# npystat (peak 0.88, finite, DC 1e-3) and a look at the regions: the 2 ms
# attack ramps, the hat stops within the 5 ms fade of its release.
set(_kit2 "${ARB_AUDIO_ROOT}/tests/golden/kit/kit_v2.json")
set(_notes2 "${CMAKE_CURRENT_SOURCE_DIR}/tests/notes_pads.txt")
add_test(NAME drum_pads_golden COMMAND arbutus-run "${_box}"
         --notes "${_notes2}" --len 48000 --sr 48000 --frames 512
         --asset kit=${_kit2} --env 0.002 0.3 0 0
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/drum_pads.npy" --tol -100)
add_test(NAME ragged_drum_pads
         COMMAND "${ARB_AUDIO_ROOT}/tests/ragged.sh"
                 $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged_pads"
                 --notes "${_notes2}" --len 48000 --sr 48000 --asset kit=${_kit2} --env 0.002 0.3 0 0)
