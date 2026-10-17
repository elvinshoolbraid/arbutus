# Contributing

Pull requests and feature suggestions are both welcome.

- **A change:** open a pull request. Say what it does and how you checked it (a test, a
  screenshot of the emulator, a sound). Small and focused merges fastest.
- **An idea or a bug:** open an issue. Feature suggestions are wanted, half-formed ones too.
- **A new sound engine:** start from `audio/sdk/template/`; an engine lives in its own
  folder and needs nothing else in the tree changed.

Elvin, Arbutus Audio's founder, reviews every pull request and decides what merges.

## Sign off every commit

Every commit in a pull request must be signed off: commit with `git commit -s`, which adds a
line like this to the message, with your name and email as the commit's author:

    Signed-off-by: Your Name <you@example.com>

That line says you agree to the Developer Certificate of Origin
(https://developercertificate.org/): in short, that you wrote the change or otherwise have
the right to submit it under this repository's open-source licences, and that you understand
it and your sign-off are public and kept for good. A check on every pull request fails if any
commit lacks the line (`.github/workflows/dco.yml`). To sign off commits you already made:
`git rebase --signoff main`, then push again (`git push --force-with-lease`).

**How this repository is kept:** it is exported from Arbutus Audio's private working repo
(which also holds the hardware and the business). A pull request merged here is re-applied
there, and the next export carries it back; you may see your change arrive in a later commit
rather than as your own merge commit.

**Licence:** what you contribute is under the licence of the folder it lands in
([LICENSING.md](LICENSING.md)): GPL-3.0-or-later for most of the tree, MIT for the engine SDK
(`audio/sdk/`), CC BY-SA 4.0 for `docs/`.
