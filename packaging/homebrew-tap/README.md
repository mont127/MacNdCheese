This directory is the source template for the `macndcheese` Homebrew cask.

The live tap is [realmaitreal/homebrew-macndcheese](https://github.com/realmaitreal/homebrew-macndcheese).
`.github/workflows/homebrew-bump.yml` renders `Casks/macndcheese.rb` (substituting
the new `version`/`sha256`) and pushes it there automatically on every stable
(`vX.Y.Z`) release — nightlies and `auto-*` dispatch builds are skipped.

To install:

```
brew tap realmaitreal/macndcheese
brew install --cask macndcheese
```

To change the cask itself (name, caveats, zap paths, etc.), edit
`Casks/macndcheese.rb` here and open a PR — the next release will push your
change out through the tap automatically. The `version`/`sha256` lines get
overwritten by the workflow each release, so don't worry about keeping those
current by hand.
