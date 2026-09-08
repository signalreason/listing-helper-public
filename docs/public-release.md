# Prepare a public release

Keep the repository private until this procedure is complete. Source cleanup does not
change visibility or remove old history. The owner selected a new repository containing
only the reviewed source snapshot and confirmed the MIT license. Retain the original
repository privately. Do not copy its Git history, issues, comments, branches, or tags.

## Source checks

1. Include the owner-approved [MIT License](../LICENSE) in the source snapshot.
2. Run `bin/validate-repo`, browser tests, and `bin/check-secrets` on the exact release
   revision. Review findings privately. A clean scan cannot prove no sensitive data exists.
3. Review examples and screenshots. Use invented data. Keep deployment domains,
   credentials, contact details, real photos, database exports, and private listing data
   outside Git. The privacy page gets public contact details from runtime settings.
4. Review Git author and committer identities, commit messages, and every branch or tag
   that will be public. Coordinate with owners of active branches before inspecting or
   changing them. The normal scan covers only the current checkout's reachable history.
5. If credentials are found, revoke or rotate them first. Remove sensitive history using
   [GitHub's procedure](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).
   Deleting a file in a new commit is not sufficient. Do not merge old history back into
   a cleaned repository.

## GitHub checks

Review issue and pull request bodies, review comments, attachments, releases, Actions
logs and artifacts, wiki content, and repository metadata before a visibility change.
Do not put a raw audit report in a public issue. A fresh repository should receive only
the reviewed snapshot, not private branches, tags, or discussion history.

Enable secret scanning, push protection, and private vulnerability reporting where
available. Confirm **Report a vulnerability** works before release. Require `validate`
and `secrets` checks for public main. Review workflow and scanner changes carefully.

Use a reseller-focused description, for example:

> Turn item photos into reviewed eBay listing drafts. Run your own browser-based app.

Do not link a private installation as a public demo. Changing visibility also exposes
Actions history and logs; see
[GitHub visibility guidance](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility).

## Deployment and handoff

Set the existing installation's privacy contact settings before deploying this code,
then confirm the page shows the correct operator. See the
[privacy settings migration](../.do/README.md#existing-installation-privacy-settings-migration).
Do not apply the example app specification over a configured live application.

Test setup with a clean database and runtime. Record local checks separately from hosted
setup. Do not claim eBay Production acceptance or DigitalOcean provisioning passed unless
those checks ran. Obtain the owner's final approval before changing visibility or
replacing history.
