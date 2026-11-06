# Release Checklist

This document outlines best practices for releasing new versions of OntoMonster.

## Community Health Files ✅

- [x] LICENSE (BSD-3-Clause)
- [x] NOTICE (LLNL auspice and disclaimer)
- [x] README.md (project summary and usage)
- [x] CODE_OF_CONDUCT.md (community standards)
- [x] CONTRIBUTING.md (contribution guidelines)
- [x] LLNL-CODE-2026540 release number

## Version Tagging

When ready to release a new version:

1. **Update version number** in:
   - `pyproject.toml` (version field)
   - `README.md` (Current version section)
   - `CHANGELOG.md` (add new version entry)

2. **Create and push a git tag**:
   ```bash
   git tag -a v1.0.0 -m "OntoMonster 1.0.0 - Initial public release"
   git push origin v1.0.0
   ```

3. **Create a GitHub Release**:
   - Go to https://github.com/Jay2-davic/ontomonster/releases/new
   - Select the tag you created
   - Add release notes (copy from CHANGELOG.md)
   - Publish release

## DOE CODE Submission

Submit to DOE CODE for discoverability:

1. Visit https://www.osti.gov/doecode/
2. Log in with DOE credentials
3. Submit OntoMonster with:
   - **Project Title**: OntoMonster
   - **DOE Contract**: DE-AC52-07NA27344 (LLNL)
   - **License**: BSD-3-Clause
   - **Repository**: https://github.com/Jay2-davic/ontomonster
   - **Release Number**: LLNL-CODE-2026540
4. After approval, add the DOI to README.md:
   ```markdown
   [![DOI](https://img.shields.io/badge/DOI-10.11578%2Fdc.xxxxxxxx-blue)](https://doi.org/10.11578/dc.xxxxxxxx)
   ```

## Journal of Open Source Software (JOSS)

OntoMonster qualifies as research software. Consider submitting to JOSS:

### Requirements
- [x] Open source license (BSD-3-Clause)
- [x] README with installation and usage
- [x] Statement of need (in docs/architecture.md)
- [ ] Example usage (add to docs if submitting)
- [ ] Tests (add if submitting)
- [ ] Community guidelines (CODE_OF_CONDUCT.md, CONTRIBUTING.md)
- [ ] API documentation (consider adding)

### Submission Process
1. Visit https://joss.theoj.org/
2. Prepare a `paper.md` file following JOSS format
3. Submit via GitHub issue
4. Respond to reviewer feedback
5. After acceptance, add citation badge to README:
   ```markdown
   [![DOI](https://joss.theoj.org/papers/10.21105/joss.xxxxx/status.svg)](https://doi.org/10.21105/joss.xxxxx)
   ```

## Read the Docs

Consider setting up documentation on Read the Docs:

1. Visit https://readthedocs.org/
2. Import project from GitHub
3. Configure build:
   - Python version
   - Requirements file
   - Build docs from `docs/` folder
4. Add badge to README:
   ```markdown
   [![Documentation Status](https://readthedocs.org/projects/ontomonster/badge/?version=latest)](https://ontomonster.readthedocs.io/en/latest/)
   ```

## Pre-Release Checklist

Before each release:

- [ ] All tests pass
- [ ] Code style checks pass (`pycodestyle src/ontomonster`)
- [ ] Documentation is up to date
- [ ] CHANGELOG.md is updated
- [ ] Version numbers are updated
- [ ] Screenshots are current (run `pixi run dev-screenshots`)
- [ ] No sensitive information in code or docs
- [ ] LICENSE and NOTICE files are current

## Post-Release

After releasing:

- [ ] Announce on relevant channels (team Slack, email lists)
- [ ] Update internal documentation
- [ ] Submit to DOE CODE (first release)
- [ ] Consider JOSS submission (after stabilization)
- [ ] Monitor GitHub issues for feedback

---

**Current Status:**
- Version: 1.0.0
- Release Date: 2024
- DOE CODE: Pending
- JOSS: Not submitted
- Read the Docs: Not configured
