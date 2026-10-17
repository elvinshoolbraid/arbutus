add_arbutus_engine(arp SOURCES src/engine.cpp)

set(_box "${ARB_BOX_ROOT}/arbutus/arp")
set(_notes "${CMAKE_CURRENT_SOURCE_DIR}/tests/notes.txt")

# A sequencer's golden is its event list, not audio (§9.4 by extension), as
# `endless`: --events-out dumps what it emitted, rebased onto the run's timeline.
function(arb_arp_events case)
  set(_out "${CMAKE_CURRENT_BINARY_DIR}/${case}.json")
  add_test(NAME arp_${case}_events COMMAND arbutus-run "${_box}"
           --notes "${_notes}" --len 48000 --sr 48000 --frames 512 --tempo 120
           --seed 7 --events-out "${_out}" ${ARGN})
  add_test(NAME arp_${case}_golden COMMAND ${CMAKE_COMMAND} -E compare_files
           "${_out}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/${case}.json")
  set_tests_properties(arp_${case}_events PROPERTIES FIXTURES_SETUP arp_${case}_dump)
  set_tests_properties(arp_${case}_golden PROPERTIES FIXTURES_REQUIRED arp_${case}_dump)
endfunction()

# Defaults: UP, one octave, 1/16, GATE 80 %.
arb_arp_events(up)
# Two octaves of UPDN with swing on, and RATCHET 2 -- the three things that
# change the unit of time rather than the order.
arb_arp_events(updn --set mode=2 --set oct=2 --set swing=66 --set ratchet=2)
# ORDER plays the chord as it was played, not as it sorts; HOLD keeps it
# running after the keys are up.
arb_arp_events(order --set mode=4 --set hold=1)

add_test(NAME rt_guard_arp COMMAND arbutus-run "${_box}" --rt-guard
         --notes "${_notes}" --len 48000 --sr 48000 --tempo 120
         --set mode=5 --set oct=4 --set ratchet=4 --set hold=1)

# Rule 8 for what a sequencer makes: its events, at every block size, with a
# tempo that puts no step on a round frame (#631, test 10).
add_test(NAME ragged_events_arp
         COMMAND "${ARB_AUDIO_ROOT}/tests/ragged_events.sh"
                 $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged_events"
                 --notes "${_notes}" --len 96000 --sr 48000 --tempo 97
                 --set swing=63 --set gate=37 --set mode=2 --set oct=2 --set ratchet=3)
