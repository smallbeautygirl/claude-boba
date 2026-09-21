Generate a Conventional Commits message for the staged changes in this repository.

Steps:

1. Run `git diff --cached` to see what is staged. If nothing is staged, run
   `git diff` and tell the user to stage changes first.

2. Read `.claude/rules/git-commits.md` for the type/scope/emoji tables — do not
   work from memory, the scope list is project-specific.

3. Draft the message:

   ```
   <type>(<scope>): <emoji> <description>

   <body — explain WHY, not what. Only if non-trivial.>

   <footers — BREAKING CHANGE, Refs. Only if relevant.>
   ```

   - Description: imperative mood, max 72 chars, no period
   - Body: one blank line after the description, explains motivation
   - Breaking changes: `!` before the colon AND a `BREAKING CHANGE:` footer

4. Show the message to the user and ask for confirmation before committing.

5. On confirmation, run `git commit -m "<message>"`. If the user declines, do
   not commit — let them edit the message or re-stage.

Never pass `--no-verify`. If a hook fails, fix the cause; the hooks here include
a secret scan and this project handles three sets of credentials.
