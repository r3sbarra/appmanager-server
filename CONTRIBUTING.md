# Contributing to AppManager

Thank you for your interest in contributing to **AppManager**! We welcome bug reports, documentation improvements, feature suggestions, and pull requests.

---

## Code of Conduct

All contributors and maintainers are expected to abide by our [Code of Conduct](CODE_OF_CONDUCT.md). Please read it before participating.

---

## Development Setup

### 1. Fork and Clone the Repository

```bash
git clone https://github.com/appmanager/appmanager.git
cd appmanager
```

### 2. Create and Activate a Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Development Dependencies

Install AppManager in editable mode along with development and documentation packages:

```bash
pip install -e ".[dev,docs]"
```

### 4. Setup Configuration and Seed Sample Data

```bash
cp .env.example .env
appmanager seed
```

### 5. Start Local Development Server

```bash
appmanager run
```

Access the host portal at `http://localhost:5000`.

---

## Running Tests and Linting

Before submitting a pull request, ensure all tests pass and your code complies with project style guidelines:

### Run Unit and Integration Tests

```bash
pytest
```

To run with coverage:

```bash
pytest --cov=appmanager --cov-report=term-missing
```

### Code Formatting and Linting

We use [Ruff](https://github.com/astral-sh/ruff) for linting and code style:

```bash
ruff check .
ruff format --check .
```

---

## Documentation

Documentation is built using [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/).

To preview the documentation locally:

```bash
mkdocs serve
```

Open `http://127.0.0.1:8000` in your browser.

To verify strict documentation build without broken links:

```bash
mkdocs build --strict
```

---

## Submitting Pull Requests

1. Create a descriptive feature branch:
   ```bash
   git checkout -b feature/my-new-feature
   ```
2. Commit your changes with clear, semantic commit messages.
3. Add or update tests covering your changes.
4. Ensure all tests pass (`pytest`) and linting succeeds (`ruff check .`).
5. Push your branch to your fork:
   ```bash
   git push origin feature/my-new-feature
   ```
6. Open a Pull Request on GitHub describing the motivation and changes made.
