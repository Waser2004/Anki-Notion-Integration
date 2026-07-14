# Release-note images

Place optional release images in this directory and reference them from the root
`CHANGELOG.md`, for example:

```markdown
![Overview of the new interface](docs/release-notes-assets/1.4.0-overview.png)
```

Use descriptive alternative text and reasonably sized PNG, JPEG, GIF, or SVG files.
All deployment scripts copy this directory into the add-on, so relative image links
render without internet access.
