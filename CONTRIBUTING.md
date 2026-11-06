# Contributing to OntoMonster

Thank you for your interest in contributing to OntoMonster! This document provides guidelines for contributing to the project.

## Code of Conduct

This project adheres to the [Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code. Please report unacceptable behavior to the project maintainers.

## License

OntoMonster is distributed under the BSD-3-Clause license. All contributions must be made under this license.

By submitting a pull request, you certify that:
- Your contribution is made under the BSD-3-Clause license
- You have the right to submit the contribution under this license
- You agree that your contribution may be distributed under the project's license

This follows the "inbound=outbound" model where contributions are made under the same license as the project is distributed.

## Getting Started

1. **Fork the repository** on GitHub
2. **Clone your fork** locally:
   ```bash
   git clone https://github.com/YOUR-USERNAME/ontomonster.git
   cd ontomonster
   ```
3. **Set up your development environment**:
   ```bash
   # Using Pixi (recommended)
   pixi install
   
   # Or using pip
   pip install -e .
   pip install playwright pycodestyle  # dev dependencies
   ```

4. **Run OntoMonster**:
   ```bash
   ontomonster
   ```

## Development Workflow

### Creating a Branch

Create a feature branch for your work:
```bash
git checkout -b feature/your-feature-name
```

Use descriptive branch names:
- `feature/` for new features
- `fix/` for bug fixes
- `docs/` for documentation changes
- `refactor/` for code refactoring

### Making Changes

1. **Write clear, concise code** following the project's style
2. **Add tests** if applicable
3. **Update documentation** if you change functionality
4. **Run style checks**:
   ```bash
   pixi run style-check  # or: pycodestyle src/ontomonster
   ```

### Commit Guidelines

Write clear commit messages following this format:
```
Short summary (50 chars or less)

Longer explanation if needed (wrap at 72 characters). Explain what
changed and why, not how.

- Bullet points are okay
- Use present tense: "Add feature" not "Added feature"
```

**Important**: All commits must include the SPDX license identifier. Ensure all new source files include:
```python
# SPDX-License-Identifier: BSD-3-Clause
```

### Submitting a Pull Request

1. **Push your branch** to your fork:
   ```bash
   git push origin feature/your-feature-name
   ```

2. **Open a Pull Request** on GitHub:
   - Provide a clear title and description
   - Reference any related issues
   - Explain what changed and why
   - Include screenshots for UI changes

3. **Respond to feedback**:
   - Address reviewer comments
   - Make requested changes
   - Push updates to your branch

4. **After approval**, a maintainer will merge your PR

## Development Guidelines

### Code Style

- Follow [PEP 8](https://pep8.org/) style guide
- Maximum line length: 79 characters for code, 72 for comments/docstrings
- Use meaningful variable names
- Add docstrings for functions and classes
- Run `pycodestyle` before committing

### Testing

- Test your changes locally before submitting
- Run the application and verify functionality:
  ```bash
  pixi run start  # or: ./run.sh
  ```
- If you modify the UI, test in multiple browsers
- For data import features, test with sample files

### Documentation

- Update the [User Guide](docs/GUIDE.md) for user-facing changes
- Update [Architecture](docs/architecture.md) for design changes
- Update [CHANGELOG.md](CHANGELOG.md) with notable changes
- Regenerate screenshots if UI changes (see [docs/MAINTAINING.md](docs/MAINTAINING.md)):
  ```bash
  pixi run dev-screenshots
  ```

## Source File Headers

All source files should include:
```python
# Copyright 2024-2025 Lawrence Livermore National Security, LLC and OntoMonster contributors.
# SPDX-License-Identifier: BSD-3-Clause
```

## Reporting Issues

Found a bug or have a feature request?

1. **Check existing issues** to avoid duplicates
2. **Open a new issue** with:
   - Clear, descriptive title
   - Steps to reproduce (for bugs)
   - Expected vs actual behavior
   - Your environment (OS, Python version)
   - Screenshots if applicable

## Questions?

- Read the [User Guide](docs/GUIDE.md)
- Check existing [Issues](https://github.com/Jay2-davic/ontomonster/issues)
- Open a new issue for questions

## Recognition

Contributors are recognized in:
- Git commit history
- GitHub contributors page
- Project acknowledgments

Thank you for contributing to OntoMonster!

---

**LLNL Release Number:** LLNL-CODE-2026540
