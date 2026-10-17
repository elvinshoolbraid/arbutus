# The stage-1 suite: the SDK's own behaviour, then every box proved against a
# golden. `ctest --test-dir build --output-on-failure` is the whole gate.

add_executable(test_poly test_poly.cpp)
target_link_libraries(test_poly PRIVATE arb_sdk)
add_test(NAME poly COMMAND test_poly)

set(_biquad "${ARB_BOX_ROOT}/arbutus/biquad")
set(_example "${ARB_BOX_ROOT}/arbutus/example")
set(_notes "${ARB_AUDIO_ROOT}/sdk/template/tests/notes.txt")

add_test(NAME manifest_refusals
         COMMAND "${CMAKE_CURRENT_SOURCE_DIR}/refusals.sh"
                 $<TARGET_FILE:arbutus-run> "${_biquad}" "${CMAKE_CURRENT_BINARY_DIR}/refuse"
                 "${ARB_ARCH}")

# §3.9: arb-manifest refuses an id that is not the box's folder (#675).
add_test(NAME manifest_id
         COMMAND "${CMAKE_CURRENT_SOURCE_DIR}/manifest_id.sh"
                 $<TARGET_FILE:arb-manifest> $<TARGET_FILE:example>
                 "${ARB_AUDIO_ROOT}/sdk/template/engine.json"
                 "${CMAKE_CURRENT_BINARY_DIR}/manifest_id" "${ARB_ARCH}")

# Same engine, different block sizes, same samples.
add_test(NAME ragged_biquad
         COMMAND "${CMAKE_CURRENT_SOURCE_DIR}/ragged.sh"
                 $<TARGET_FILE:arbutus-run> "${_biquad}" "${CMAKE_CURRENT_BINARY_DIR}/ragged_biquad"
                 --in "${ARB_AUDIO_ROOT}/tests/golden/python/biquad_input.npy"
                 --set cutoff=1200 --set q=0.9 --sr 48000)

# ragged_example and rt_guard_example live in sdk/template/CMakeLists.txt since
# #675, so a copy of the template carries its three tests with it.

# ...and the negative control (#56). The 24 ragged_* tests could not fail for as
# long as they had existed -- the script tested `sed`'s exit status -- so the
# harness now proves it can refuse, against a box built outside ARB_BOX_ROOT
# whose output really does depend on the block size. Same shape as
# rt_guard_catches_allocation below, and for the same reason.
add_arbutus_engine(blockbox SOURCES blockbox/src/engine.cpp
                   MANIFEST "${CMAKE_CURRENT_SOURCE_DIR}/blockbox/engine.json"
                   ROOT "${CMAKE_BINARY_DIR}/raggedfix")
add_test(NAME ragged_catches_block_dependence
         COMMAND "${CMAKE_CURRENT_SOURCE_DIR}/ragged_fails.sh"
                 "${CMAKE_CURRENT_SOURCE_DIR}/ragged.sh" $<TARGET_FILE:arbutus-run>
                 "${CMAKE_BINARY_DIR}/raggedfix/arbutus-test/blockbox"
                 "${CMAKE_CURRENT_BINARY_DIR}/ragged_blockbox")

# The same control for ragged_events.sh, the sequencers' version (#631):
# `seqblockbox` plays a note per process() call, so its events depend on the
# block size and the script must refuse it.
add_arbutus_engine(seqblockbox SOURCES seqblockbox/src/engine.cpp
                   MANIFEST "${CMAKE_CURRENT_SOURCE_DIR}/seqblockbox/engine.json"
                   ROOT "${CMAKE_BINARY_DIR}/raggedfix")
add_test(NAME ragged_events_catches_block_dependence
         COMMAND "${CMAKE_CURRENT_SOURCE_DIR}/ragged_events_fails.sh"
                 "${CMAKE_CURRENT_SOURCE_DIR}/ragged_events.sh" $<TARGET_FILE:arbutus-run>
                 "${CMAKE_BINARY_DIR}/raggedfix/arbutus-test/seqblockbox"
                 "${CMAKE_CURRENT_BINARY_DIR}/ragged_seqblockbox")

# ...and the NaN control (#626): the golden comparison skipped NaN
# (`std::max(peak, NaN)` is `peak`), so an all-NaN engine passed every golden
# and every ragged_* test. `nanbox` emits one NaN on demand; arbutus-run must
# refuse it with a golden, against a NaN golden, with none, and in ragged.sh.
add_arbutus_engine(nanbox SOURCES nanbox/src/engine.cpp
                   MANIFEST "${CMAKE_CURRENT_SOURCE_DIR}/nanbox/engine.json"
                   ROOT "${CMAKE_BINARY_DIR}/nanfix")
add_test(NAME arbutus_run_catches_nan
         COMMAND "${CMAKE_CURRENT_SOURCE_DIR}/nan_fails.sh"
                 $<TARGET_FILE:arbutus-run> "${CMAKE_CURRENT_SOURCE_DIR}/ragged.sh"
                 "${CMAKE_BINARY_DIR}/nanfix/arbutus-test/nanbox"
                 "${CMAKE_CURRENT_BINARY_DIR}/nanbox")

# §3.7.1: no allocation after create(). Cheap enough to assert every run.
add_test(NAME rt_guard_biquad COMMAND arbutus-run "${_biquad}" --rt-guard
         --in "${ARB_AUDIO_ROOT}/tests/golden/python/biquad_input.npy" --sr 48000)

# ...and the negative control (#52): a box that allocates every block, built
# outside ARB_BOX_ROOT so the host never indexes it and the UI never lists it.
# The guard has to refuse it, on every platform that claims it can count.
add_arbutus_engine(allocbox SOURCES allocbox/src/engine.cpp
                   MANIFEST "${CMAKE_CURRENT_SOURCE_DIR}/allocbox/engine.json"
                   ROOT "${CMAKE_BINARY_DIR}/rtguard")
add_test(NAME rt_guard_catches_allocation
         COMMAND "${CMAKE_CURRENT_SOURCE_DIR}/rt_guard_alloc.sh"
                 $<TARGET_FILE:arbutus-run> "${CMAKE_BINARY_DIR}/rtguard/arbutus-test/allocbox")

# §9.4: the Python voice goldens (they live in tests/golden/python/ since the
# emulator went native-only), replayed against the
# headers the stage-3 synth boxes are built from. The boxes' own parameter
# ranges cannot reach the golden's values (DDR-044), so the test drives the
# cores directly -- see the file's header comment.
add_executable(test_voice_golden test_voice_golden.cpp)
target_link_libraries(test_voice_golden PRIVATE arb_sdk arb_hostlib)
add_test(NAME voice_goldens COMMAND test_voice_golden
         "${ARB_AUDIO_ROOT}/tests/golden/python")

# ABI 2: arb::Smoothed, realtime rule 7 (§15.7).
add_executable(test_params test_params.cpp)
target_link_libraries(test_params PRIVATE arb_sdk)
add_test(NAME params COMMAND test_params)

# ABI 2 descriptor fields: builders, engine.json round trip, loader check.
add_executable(test_manifest test_manifest.cpp)
target_link_libraries(test_manifest PRIVATE arb_sdk arb_hostlib)
target_compile_definitions(test_manifest PRIVATE ARB_HOST_ARCH="${ARB_ARCH}")
add_test(NAME manifest COMMAND test_manifest)

# The audio thread's event sort (host/events.hpp): stable, and it allocates
# nothing -- checked with a counting operator new, which is the one thing
# --rt-guard's net-growth count cannot see (audit Q1, 2026-09-18).
add_executable(test_events test_events.cpp)
target_link_libraries(test_events PRIVATE arb_sdk)
target_include_directories(test_events PRIVATE "${ARB_AUDIO_ROOT}/host")
add_test(NAME events COMMAND test_events)

# The tape's range table and its storage seam (C1 step 1). Links tape.cpp and
# tape_store.cpp rather than the host, for test_imu's reason: a broken
# audio_host.cpp must not be able to hide a broken medium. Counts `operator new`
# over the audio path, and every alarm here has a negative control beside it.
add_executable(test_tape test_tape.cpp "${ARB_AUDIO_ROOT}/host/tape.cpp"
                        "${ARB_AUDIO_ROOT}/host/tape_store.cpp")
target_include_directories(test_tape PRIVATE "${ARB_AUDIO_ROOT}/host")
add_test(NAME tape COMMAND test_tape)

# The write-behind: the ring, the I/O thread, the lagging 4 KiB header and the
# per-range commit (C1 step 2a). Links the three tape .cpp files and hostlib
# (for `read_wav`, which is what says the live header is a file every reader
# opens) rather than the host, for the reason above.
add_executable(test_tape_io test_tape_io.cpp "${ARB_AUDIO_ROOT}/host/tape.cpp"
                           "${ARB_AUDIO_ROOT}/host/tape_store.cpp"
                           "${ARB_AUDIO_ROOT}/host/tape_io.cpp")
find_package(Threads REQUIRED)
target_link_libraries(test_tape_io PRIVATE arb_hostlib Threads::Threads)
target_include_directories(test_tape_io PRIVATE "${ARB_AUDIO_ROOT}/host")
add_test(NAME tape_io COMMAND test_tape_io)

# #825: `PosixStore` with a commit and an open in it at once, and ENOSPC heard
# by the thread whose call met it. Under ThreadSanitizer where the compiler
# has it, so a race in the handle table fails the test (exit 66) every run.
include(CheckCXXSourceCompiles)
set(CMAKE_REQUIRED_FLAGS "-fsanitize=thread")
set(CMAKE_REQUIRED_LINK_OPTIONS "-fsanitize=thread")
check_cxx_source_compiles("int main() { return 0; }" ARB_HAVE_TSAN)
unset(CMAKE_REQUIRED_FLAGS)
unset(CMAKE_REQUIRED_LINK_OPTIONS)
add_executable(test_tape_store_threads test_tape_store_threads.cpp
                                       "${ARB_AUDIO_ROOT}/host/tape_store.cpp")
target_link_libraries(test_tape_store_threads PRIVATE Threads::Threads)
target_include_directories(test_tape_store_threads PRIVATE "${ARB_AUDIO_ROOT}/host")
if(ARB_HAVE_TSAN)
    target_compile_options(test_tape_store_threads PRIVATE -fsanitize=thread)
    target_link_options(test_tape_store_threads PRIVATE -fsanitize=thread)
endif()
add_test(NAME tape_store_threads COMMAND test_tape_store_threads)

# C1 step 2b: the bounded pool -- eviction, fetching a range back, and the take
# that ends when a stalled card has turned the pool into write queue.
add_executable(test_tape_pool test_tape_pool.cpp "${ARB_AUDIO_ROOT}/host/tape.cpp"
                             "${ARB_AUDIO_ROOT}/host/tape_store.cpp"
                             "${ARB_AUDIO_ROOT}/host/tape_io.cpp")
target_link_libraries(test_tape_pool PRIVATE arb_hostlib Threads::Threads)
target_include_directories(test_tape_pool PRIVATE "${ARB_AUDIO_ROOT}/host")
add_test(NAME tape_pool COMMAND test_tape_pool)

# C1 step 5: the wanted set -- read-ahead as a window on the timeline, the loop
# seam, the pool/2 cap, and T3/T5.
add_executable(test_tape_readahead test_tape_readahead.cpp "${ARB_AUDIO_ROOT}/host/tape.cpp"
                                  "${ARB_AUDIO_ROOT}/host/tape_store.cpp"
                                  "${ARB_AUDIO_ROOT}/host/tape_io.cpp")
target_link_libraries(test_tape_readahead PRIVATE arb_hostlib Threads::Threads)
target_include_directories(test_tape_readahead PRIVATE "${ARB_AUDIO_ROOT}/host")
add_test(NAME tape_readahead COMMAND test_tape_readahead)

# C1 step 6's follow-up (#163): the peaks sidecar -- the bins agree with a scan
# of the samples, the three refusals that keep that true, the file, and a stale
# one discarded.
add_executable(test_tape_peaks test_tape_peaks.cpp "${ARB_AUDIO_ROOT}/host/tape.cpp"
                              "${ARB_AUDIO_ROOT}/host/tape_store.cpp"
                              "${ARB_AUDIO_ROOT}/host/tape_io.cpp")
target_link_libraries(test_tape_peaks PRIVATE arb_hostlib Threads::Threads)
target_include_directories(test_tape_peaks PRIVATE "${ARB_AUDIO_ROOT}/host")
add_test(NAME tape_peaks COMMAND test_tape_peaks)

# The crash-safe writer `tape_save` uses (#93): temp file, fsync, rename, and
# a refused write that leaves the previous save whole -- with plain `write` as
# the negative control beside it.
add_executable(test_audiofile test_audiofile.cpp)
target_link_libraries(test_audiofile PRIVATE arb_hostlib)
add_test(NAME audiofile COMMAND test_audiofile)

# sdk/wav.hpp against hostlib's reader: the same files, the same samples (#675).
add_executable(test_wav test_wav.cpp)
target_link_libraries(test_wav PRIVATE arb_sdk arb_hostlib)
add_test(NAME wav COMMAND test_wav)

# § 6.5's fake-hardware test for the LSM6DSO (P6, #22): a register map in
# memory, no board. It links the driver's own .cpp rather than the host, so a
# broken audio_host.cpp cannot hide a broken IMU.
add_executable(test_imu test_imu.cpp "${ARB_AUDIO_ROOT}/host/imu_lsm6dso.cpp")
target_include_directories(test_imu PRIVATE "${ARB_AUDIO_ROOT}/host")
add_test(NAME imu COMMAND test_imu)

# The key thread's state machine (#638, device-os-spec § 1.3.4 part 8 step 2):
# bytes in, notes and UI lines out, and no operator new over 10 000 lines. It
# links the reader's own .cpp, for test_imu's reason.
add_executable(test_keys_reader test_keys_reader.cpp "${ARB_AUDIO_ROOT}/host/keys_reader.cpp")
target_include_directories(test_keys_reader PRIVATE "${ARB_AUDIO_ROOT}/host")
# #1020: the line vectors uart_input.py's tests read too.
target_compile_definitions(test_keys_reader PRIVATE
    ARB_KEY_LINES="${ARB_AUDIO_ROOT}/../arbutus_os/drivers/key_lines.tsv")
add_test(NAME keys_reader COMMAND test_keys_reader)

# #743: the sample format the device is asked for (s32 for a named PCM, P3's
# WM8904; float otherwise), the config handed to ma_device_init (#811) and the
# callback's int32 conversions. Headers only: miniaudio's declarations, no
# implementation.
add_executable(test_sample_format test_sample_format.cpp)
target_include_directories(test_sample_format PRIVATE "${ARB_AUDIO_ROOT}/host")
target_include_directories(test_sample_format SYSTEM PRIVATE "${ARB_AUDIO_ROOT}/third_party/miniaudio")
add_test(NAME sample_format COMMAND test_sample_format)

# The ABI header must compile as C99 and as C++20 and mean the same thing.
add_executable(test_abi test_abi.cpp abi_c.c)
target_link_libraries(test_abi PRIVATE arb_sdk)
add_test(NAME abi COMMAND test_abi)

# #631: the sequencer and modulator boxes when a parameter changes between
# blocks or a key comes up mid-gesture -- what the event goldens cannot reach,
# since `arbutus-run --set` fixes parameters before the render.
add_executable(test_seqmod test_seqmod.cpp)
target_link_libraries(test_seqmod PRIVATE arb_sdk arb_hostlib)
add_dependencies(test_seqmod arp_box euclid_box endless_box pattern_box envelope_box lfo_box)
add_test(NAME seqmod_midrender COMMAND test_seqmod "${ARB_BOX_ROOT}")

# ---- stage 2: arbutus-audio ------------------------------------------------
# Every host test runs --offline, so the suite needs no audio device. Each one
# starts its own arbutus-audio on a private socket and shuts it down again.
add_subdirectory(boxes/spin)
add_subdirectory(boxes/abi2)
add_subdirectory(boxes/constmod)
add_subdirectory(boxes/sidemod)
add_subdirectory(boxes/pitchprobe)

find_package(Python3 COMPONENTS Interpreter REQUIRED)

# #646: a MONO or LEGATO note change keeps the voice's phase in every PolyHost
# synth box: no click at the change. arbutus-run renders; no host.
add_test(NAME legato_click COMMAND ${Python3_EXECUTABLE}
         "${CMAKE_CURRENT_SOURCE_DIR}/legato_click.py"
         --run $<TARGET_FILE:arbutus-run>
         --boxes "${ARB_BOX_ROOT}"
         --audio-root "${ARB_AUDIO_ROOT}")

# #647: the string's DECAY is the fundamental's T60 and DAMP does not detune,
# up to C7 and not only at A4; the bow leaves no low end. No host.
add_test(NAME string_tuning COMMAND ${Python3_EXECUTABLE}
         "${CMAKE_CURRENT_SOURCE_DIR}/string_tuning.py"
         --run $<TARGET_FILE:arbutus-run>
         --boxes "${ARB_BOX_ROOT}")

# #647: noise's CUTOFF and TYPE and the pitch move a sounding note; drum and
# dbox follow pitch_st; dbox's HAT choke fades 5 ms. No host.
add_test(NAME drums_noise_live COMMAND ${Python3_EXECUTABLE}
         "${CMAKE_CURRENT_SOURCE_DIR}/drums_noise_live.py"
         --run $<TARGET_FILE:arbutus-run>
         --boxes "${ARB_BOX_ROOT}"
         --audio-root "${ARB_AUDIO_ROOT}")

# #630: the FX boxes with a knob moving and a centred source, which no golden
# sees (PING, DRIVE's runaway, reverb SIZE, comb TUNE with DAMP). No host.
add_test(NAME fx_moves COMMAND ${Python3_EXECUTABLE}
         "${CMAKE_CURRENT_SOURCE_DIR}/fx_moves.py"
         --run $<TARGET_FILE:arbutus-run>
         --boxes "${ARB_BOX_ROOT}"
         --audio-root "${ARB_AUDIO_ROOT}")
set_tests_properties(fx_moves PROPERTIES TIMEOUT 300)

# #930: arbutus/plate's tail is the length DECAY reads, SIZE does not move it,
# and the longest one is >= 20 s, ten times the room's, and still falling.
add_test(NAME plate_tail COMMAND ${Python3_EXECUTABLE}
         "${CMAKE_CURRENT_SOURCE_DIR}/plate_tail.py"
         --run $<TARGET_FILE:arbutus-run>
         --boxes "${ARB_BOX_ROOT}")

# #1215: a failed allocation in the plate's create() is the host's refusal
# (ENGINE BROKEN), never an abort. Links rack.cpp, test_tape's way, with an
# operator new that refuses the plate's rings.
add_executable(test_plate_alloc test_plate_alloc.cpp "${ARB_AUDIO_ROOT}/host/rack.cpp")
target_link_libraries(test_plate_alloc PRIVATE arb_hostlib arb_sdk arb_json)
target_include_directories(test_plate_alloc PRIVATE "${ARB_AUDIO_ROOT}/host")
add_test(NAME plate_alloc COMMAND test_plate_alloc "${ARB_BOX_ROOT}")

# #1215: SIZE and PREDELAY moved on a ringing tail land on the new geometry
# and do not click.
add_test(NAME plate_moves COMMAND ${Python3_EXECUTABLE}
         "${CMAKE_CURRENT_SOURCE_DIR}/plate_moves.py"
         --run $<TARGET_FILE:arbutus-run>
         --boxes "${ARB_BOX_ROOT}")

function(add_host_test name script)
  add_test(NAME ${name} COMMAND ${Python3_EXECUTABLE}
           "${CMAKE_CURRENT_SOURCE_DIR}/${script}"
           --exe $<TARGET_FILE:arbutus-audio>
           --run $<TARGET_FILE:arbutus-run>
           --boxes "${ARB_BOX_ROOT}"
           --audio-root "${ARB_AUDIO_ROOT}")
  set_tests_properties(${name} PROPERTIES TIMEOUT 300)
endfunction()

add_host_test(host_pipeline host_pipeline.py)
add_host_test(host_rack     host_rack.py)
add_host_test(host_tape     host_tape.py)
add_host_test(host_protocol host_protocol.py)
# The client's wait for a push outlasts the host's hold of it (#536); no host needed.
add_test(NAME ctl_push_wait COMMAND ${Python3_EXECUTABLE}
         "${CMAKE_CURRENT_SOURCE_DIR}/ctl_push_wait.py")
# #1180: hostcase.scratch() cleans up (#657's host suites pass either way); no host needed.
add_test(NAME hostcase_scratch COMMAND ${Python3_EXECUTABLE}
         "${CMAKE_CURRENT_SOURCE_DIR}/hostcase_scratch.py")
add_host_test(host_xrun     host_xrun.py)
# #73: `note {probe}` -> the carrying block; onset is that block's frame 0.
add_host_test(host_probe    host_probe.py)
add_host_test(host_keys     host_keys.py)
# #788: #766's three screen-replacement races, against the key thread.
add_host_test(host_keys_replace host_keys_replace.py)
# #827: a full ring at a UI drop, PONGs after NO KEYBOARD, a real reopen.
add_host_test(host_keys_recover host_keys_recover.py)
add_host_test(host_hotswap  host_hotswap.py)

# Stage 3: the sidechain path (§6.6), first exercised by arbutus/duck.
add_host_test(host_sidechain host_sidechain.py)
# Stage 3: load_asset end to end -- patch, resolution, engine, refusals (§3.2).
add_host_test(host_assets host_assets.py)
# Stage 3: a sequencer engine in the SEQ slot driving a synth (§5, §3.6).
add_host_test(host_seq host_seq.py)
# ABI 2: handshake compatibility, display, descriptor fields, ctl_out (§15).
add_host_test(host_abi2 host_abi2.py)
# The MOD rack (modulation-spec): routes, depth maths, bypass, refusals, keys,
# host.level/pan, patch round trip, and no allocation in the block with it loaded.
add_host_test(host_mod host_mod.py)
# modulation-spec §4, §13: MOD-tab sidechain, `self`/`master`, the motion feed.
add_host_test(host_modside host_modside.py)
# modulation-spec §3.2: each stock modulator box in a MOD tab, moving a target.
add_host_test(host_modboxes host_modboxes.py)
# DDR-052: ENV glide/detune/bend_range, the bend message, host.pitch and env.glide/detune routes.
add_host_test(host_voice host_voice.py)
# Stage D: drum v2 per-pad params, choke, gate/loop, copy_pad, kit migration,
# arbutus/transient in a rack, waveform, FX tab assets, rt-guard.
add_host_test(host_drum host_drum.py)
# Stage D: arbutus/sampler v2 -- loop while held, release to END, REV, seam FADE, readouts.
add_host_test(host_sampler host_sampler.py)

# Stage N1: what the emulator's tape keys and TEMPO page need natively -- loop,
# varispeed/REV, the FILTER hold, the click, COMP, the scrub grain, MONITOR.
add_host_test(host_tapekeys host_tapekeys.py)
# Stage N1: the clip model as geometry -- clip_peaks, tape_edit, src_track,
# the append-only medium, tape_save/tape_load.
add_host_test(host_clips host_clips.py)

# Stage E1: eight tracks (DDR-060), overdub-only record and its absorb
# (DDR-056), LINK (DDR-059), clip_to_asset (§15.6).
add_host_test(host_tape8 host_tape8.py)
# C1 step 2a: the write-behind through the whole host -- `--tape-dir` makes a
# take reach the card as a WAV, and no `--tape-dir` makes none of it happen.
add_host_test(host_tapeio host_tapeio.py)
add_host_test(host_compact host_compact.py)
# C1 step 3b (#157): `tape_dir` -- the media moved into a project on SAVE, opened
# in place on LOAD, and the heads a host started over existing media takes.
add_host_test(host_tapedir host_tapedir.py)
# C1 step 7: `tape_late`, the three `TAPE FULL` reasons and `tape.pool` through
# the host -- the pushes, the 3-in-2-s flag and § 4's T10.
add_host_test(host_tapefull host_tapefull.py)
# C1 step 6: `tape_edit` streams its ranges -- a long edit runs inside a window
# far smaller than itself, and the four ops' arithmetic does not move.
add_host_test(host_tapeedit host_tapeedit.py)

# Stage H1: arp / euclid / pattern in the SEQ slot end to end, and endless's
# GATE / PATTERN / ROT arriving over the wire (engine-spec §15.4).
add_host_test(host_seqboxes host_seqboxes.py)

# Stage I1: va / string / wavetable / dbox through the host -- patches, the
# wavetable asset, ENV MODE into PolyHost, and dbox's fileless kit (§15.3).
add_host_test(host_synthboxes host_synthboxes.py)

# Stage I2: the five effect boxes through the host -- on an instrument rack and
# on the master rack, macro-bound, bypassed, and `delay` following the tempo.
add_host_test(host_fxboxes host_fxboxes.py)

# Stage F1: the mixer host -- per-track EQ (DDR-062), mute/solo (DDR-061), the
# MASTER strip (LEVEL COMP DRIVE BAL / COMP REL DRIVE TONE) and the master rack.
add_host_test(host_mixer host_mixer.py)

# Stage G: the live input as an engine (DDR-063) -- `input_source` and the
# SOURCE knob as one value, the resample bus measured, and `input` as a MOD
# sidechain following SOURCE.
add_host_test(host_input host_input.py)

# Stage J1: FILE › BOUNCE -- `clip_to_asset {track:"all", master_fx}`, the
# switch that decides whether the master rack is printed, and the tape media a
# project saves and loads.
add_host_test(host_project host_project.py)

# Stage J2: `rescan` after a browser file op (DDR-055), and the dot-prefixed
# folders the scan must never index -- `.trash/` and `.originals/`. Since D2
# (#23) it also covers `library {op}` (#62): the same rules as a message.
add_host_test(host_files host_files.py)

# D2 (#23): the control protocol over TCP behind the pairing token --
# device-os-spec.md § 9.4. The transport, not the message set.
add_host_test(host_tcp host_tcp.py)
add_host_test(host_badinput host_badinput.py)
# #597: a full command ring refuses `busy` before the state moves, never ok.
add_host_test(host_ringfull host_ringfull.py)
# #673: a wrongly typed engine.json is ENGINE BROKEN, never an abort.
add_host_test(host_badbox host_badbox.py)
# #612: every clean exit is 0, and a SIGTERM mid-take keeps the take.
add_host_test(host_exit host_exit.py)
# #1226: a killed test takes its host with it (hostcase.die_with_parent).
add_host_test(host_dies_with_parent host_dies_with_parent.py)

# P9 (#89): a client that stops reading stalls nobody but itself -- the
# accepted sockets are non-blocking and a backlog past 8 MiB is dropped.
add_host_test(host_slowclient host_slowclient.py)
# #74: the state document once a burst of edits settles, not once per edit.
add_host_test(host_statepush host_statepush.py)
# #853: the live stream -- the situation document as merge patches, frames.
add_host_test(host_live host_live.py)

# #626 row 7: a negative event time was cast to uint32_t (undefined; in practice
# it wrapped, dropped the note and rendered 95,900 frames of silence, exit 0).
# PASS_REGULAR_EXPRESSION ignores the exit status, so the refusal's text is what
# passes it: the old tool printed nothing of the kind.
add_test(NAME arbutus_run_refuses_negative_time
         COMMAND arbutus-run "${_example}" --notes "${CMAKE_CURRENT_SOURCE_DIR}/notes_negative_time.txt"
                 --sr 48000)
set_tests_properties(arbutus_run_refuses_negative_time PROPERTIES
                     PASS_REGULAR_EXPRESSION "notes_negative_time.txt:2: time \"-100\" is out of range")

# #626: arbutus-ctl --follow, xrun-watch's per-run count, latency-probe with no
# answer and bench-all's one block size, against an offline host.
add_host_test(tools_cli tools_cli.py)
