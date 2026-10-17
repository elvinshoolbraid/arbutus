# hostlib — what the host and arbutus-run both need: the box loader (§3.9,
# §7.4), the library index, and the WAV/NPY reader. Promoted out of
# tools/arbutus-run when arbutus-audio arrived, so there is exactly one loader
# and one set of refusal strings in the system.
add_library(arb_hostlib STATIC box.cpp library.cpp audiofile.cpp)
target_include_directories(arb_hostlib PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}")
target_link_libraries(arb_hostlib PUBLIC arb_sdk arb_json PRIVATE ${CMAKE_DL_LIBS})
target_compile_definitions(arb_hostlib PRIVATE ARB_HOST_ARCH="${ARB_ARCH}")

# `--rt-guard`'s heap counter (#52). An OBJECT library, not part of arb_hostlib:
# the glibc half defines the malloc family, and ELF interposition only reaches
# the whole process when those definitions sit in the *executable* -- a static
# archive member that nothing references would simply never be pulled in.
# Linked into arbutus-run and arbutus-audio; boxes never see it.
add_library(arb_rtcount OBJECT rt_alloc_count.cpp rt_alloc_hooks.c)
target_include_directories(arb_rtcount PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}")
# We *are* the malloc family in that file, so the compiler must not fold our
# calls into its own idea of one, nor warn that we redeclared its builtin.
set_source_files_properties(rt_alloc_hooks.c PROPERTIES COMPILE_OPTIONS
  "-fno-builtin-malloc;-fno-builtin-calloc;-fno-builtin-realloc;-fno-builtin-free")
