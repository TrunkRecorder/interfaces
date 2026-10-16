<!-- Adding or updating an interface? Run `./add-release <repo URL> <tag>`, then `./check interfaces/<id>.json`, and commit both interfaces/<id>.json and index.json. -->

**Interface:** <!-- repository URL and the release's tag -->

**New, or an update?** <!-- for an update: what changed -->

### For the reviewer

- [ ] The source is public, and its license allows it to be listed
- [ ] It does what it says, and sends nothing anywhere the user didn't ask for: no analytics, no outside services the description doesn't mention
- [ ] It loads nothing from elsewhere at run time: no CDN scripts, fonts or styles
- [ ] It asks for the access it needs: listen, unless it really has to change the recorder
- [ ] It works at phone width and in the browsers people use
- [ ] Its screenshots show it, as it is in this version, with nothing private in them (no real unit names or talkgroups you shouldn't share)
- [ ] The id is fitting and doesn't impersonate anyone
- [ ] For an update: read the compare from the old entry's commit to the new one
