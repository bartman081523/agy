#!/usr/bin/env bash
# ==============================================================================
# Antigravity Full-Stack Mod Installer
#
# Universal installer for the Antigravity Full-Stack Suite, including:
#   - Full-Stack Mod Launcher (agy-full, agy-modular, agy-epistemic, etc.)
#   - Universal Prompts from bartman081523/prompts
#   - Sanitized Antigravity & Claude Skills Hub
#   - Multi-Account Quota Guard & Native Bubbletea Statusline
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/google-antigravity/antigravity-cli/main/install.sh | bash
#   or locally:
#   ./install.sh [--help] [--dry-run] [--no-skills] [--no-prompts]
# ==============================================================================

set -euo pipefail

BOLD="$(tput bold 2>/dev/null || echo '')"
GREEN="$(tput setaf 2 2>/dev/null || echo '')"
CYAN="$(tput setaf 6 2>/dev/null || echo '')"
YELLOW="$(tput setaf 3 2>/dev/null || echo '')"
RED="$(tput setaf 1 2>/dev/null || echo '')"
RESET="$(tput sgr0 2>/dev/null || echo '')"

PROG_NAME="Antigravity Full-Stack Installer"
INSTALL_DIR="${HOME}/.local/share/agy-mod"
BIN_DIR="${HOME}/.local/bin"
PROMPTS_DIR="${HOME}/.gemini/antigravity-cli/prompts"
SKILLS_DIR="${HOME}/.gemini/config/skills"
CLAUDE_SKILLS_DIR="${HOME}/.claude/skills"
MOD_REPO_URL="${MOD_REPO_URL:-https://github.com/bartman081523/agy.git}"
SKILLS_REPO_URL="${SKILLS_REPO_URL:-https://github.com/bartman081523/skills.git}"
PROMPTS_REPO_URL="${PROMPTS_REPO_URL:-https://github.com/bartman081523/prompts.git}"
GITHUB_REPO="bartman081523/agy"

DRY_RUN=0
INSTALL_SKILLS=1
INSTALL_PROMPTS=1

show_help() {
    cat << EOF
${BOLD}${PROG_NAME}${RESET}

Usage:
  install.sh [OPTIONS]

Options:
  --dry-run       Preview actions without making changes
  --no-skills     Skip installing skills
  --no-prompts    Skip cloning prompts repository
  --help, -h      Show this help message
EOF
}

for arg in "$@"; do
    case "$arg" in
        --dry-run)
            DRY_RUN=1
            ;;
        --no-skills)
            INSTALL_SKILLS=0
            ;;
        --no-prompts)
            INSTALL_PROMPTS=0
            ;;
        --help|-h)
            show_help
            exit 0
            ;;
        *)
            echo "${YELLOW}Unknown option: ${arg}${RESET}" >&2
            ;;
    esac
done

cat << 'EOF'
      ▄▀▀▄        Antigravity Full-Stack Suite Installer
     ▀▀▀▀▀▀       Modular Agentic AI Architecture
    ▀▀▀▀▀▀▀▀      Epistemic · Symbolic · Socratic · SymComp · QuotaGuard
   ▄▀▀    ▀▀▄     Prompts & Skills Ecosystem
  ▄▀▀      ▀▀▄
EOF

echo ""
echo "${CYAN}${BOLD}==> Initializing Antigravity Full-Stack Installation...${RESET}"

# 1. Dependency Checks
echo "${CYAN}--> Checking system prerequisites...${RESET}"
for cmd in git python3 curl; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "${RED}Error: Required command '$cmd' is not installed.${RESET}" >&2
        exit 1
    fi
done
echo "    ✓ git, python3, curl found."

# 2. Check or install upstream Antigravity CLI
echo "${CYAN}--> Checking upstream Antigravity CLI (agy)...${RESET}"
if command -v agy >/dev/null 2>&1 || [ -x "${HOME}/.local/bin/agy" ]; then
    echo "    ✓ Upstream agy binary detected."
else
    echo "${YELLOW}    Upstream agy binary not detected in PATH.${RESET}"
    if [ "$DRY_RUN" = "0" ]; then
        echo "    Installing upstream Antigravity CLI via official installer..."
        curl -fsSL https://antigravity.google/cli/install.sh | bash || {
            echo "${YELLOW}    Notice: Official installer unavailable or failed; continuing with mod wrappers.${RESET}"
        }
    fi
fi

# 3. Determine Mod Source Location
SCRIPT_SOURCE_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    CANDIDATE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [ -d "${CANDIDATE}/agy_modules" ] && [ -d "${CANDIDATE}/bin" ]; then
        SCRIPT_SOURCE_DIR="${CANDIDATE}"
    fi
fi

mkdir -p "${BIN_DIR}"

if [ -n "${SCRIPT_SOURCE_DIR}" ]; then
    echo "${CYAN}--> Using local repository at: ${SCRIPT_SOURCE_DIR}${RESET}"
    TARGET_REPO_DIR="${SCRIPT_SOURCE_DIR}"
else
    echo "${CYAN}--> Deploying AGY Mod from GitHub to: ${INSTALL_DIR}${RESET}"
    TARGET_REPO_DIR="${INSTALL_DIR}"
    if [ "$DRY_RUN" = "0" ]; then
        INSTALLED_FROM_RELEASE=0
        mkdir -p "${INSTALL_DIR}"
        echo "    Checking latest release on GitHub (https://api.github.com/repos/${GITHUB_REPO}/releases/latest)..."
        RELEASE_JSON="$(curl -fsSL -H "User-Agent: AGY-Installer" "https://api.github.com/repos/${GITHUB_REPO}/releases/latest" 2>/dev/null || true)"
        DOWNLOAD_URL=""
        if [ -n "${RELEASE_JSON}" ]; then
            DOWNLOAD_URL="$(echo "${RELEASE_JSON}" | grep -o 'https://[^"]*agy-release\.tar\.gz' | head -n 1 || true)"
            if [ -z "${DOWNLOAD_URL}" ]; then
                DOWNLOAD_URL="$(echo "${RELEASE_JSON}" | grep -o 'https://[^"]*\.tar\.gz' | head -n 1 || true)"
            fi
        fi

        if [ -n "${DOWNLOAD_URL}" ]; then
            echo "    Downloading latest release package from: ${DOWNLOAD_URL}..."
            TMP_TAR="$(mktemp --suffix=.tar.gz)"
            if curl -fsSL -o "${TMP_TAR}" "${DOWNLOAD_URL}"; then
                echo "    Extracting release bundle..."
                tar -xzf "${TMP_TAR}" -C "${INSTALL_DIR}" --strip-components=1 2>/dev/null || tar -xzf "${TMP_TAR}" -C "${INSTALL_DIR}"
                rm -f "${TMP_TAR}"
                INSTALLED_FROM_RELEASE=1
                echo "    ✓ Extracted release bundle into ${INSTALL_DIR}"
            fi
        fi

        if [ "${INSTALLED_FROM_RELEASE}" = "0" ]; then
            if [ -d "${INSTALL_DIR}/.git" ]; then
                echo "    Updating existing mod checkout..."
                git -C "${INSTALL_DIR}" pull --ff-only || true
            else
                echo "    Cloning AGY Mod repository (${MOD_REPO_URL})..."
                git clone "${MOD_REPO_URL}" "${INSTALL_DIR}"
            fi
        fi
    fi
fi

# 4. Install Command Wrappers in ~/.local/bin
echo "${CYAN}--> Installing launcher binaries into ${BIN_DIR}...${RESET}"
LAUNCHERS=(
    "agy-full"
    "agy-modular"
    "agy-epistemic"
    "agy-socratic"
    "agy-symbolic"
    "agy-symbolic-epistemic"
    "agy-symcomp"
    "agy-infrastruktur"
    "login"
)

if [ "$DRY_RUN" = "0" ]; then
    for l in "${LAUNCHERS[@]}"; do
        SRC_BIN="${TARGET_REPO_DIR}/bin/${l}"
        if [ -f "${SRC_BIN}" ]; then
            chmod +x "${SRC_BIN}"
            ln -sf "${SRC_BIN}" "${BIN_DIR}/${l}"
            if [ "${l}" = "login" ]; then
                ln -sf "${SRC_BIN}" "${BIN_DIR}/agy-login"
            fi
            echo "    ✓ ${l} -> ${BIN_DIR}/${l}"
        fi
    done
    # Setup agy-update symlink
    if [ -f "${TARGET_REPO_DIR}/bin/agy-full" ]; then
        ln -sf "${TARGET_REPO_DIR}/bin/agy-full" "${BIN_DIR}/agy-update"
        echo "    ✓ agy-update -> ${BIN_DIR}/agy-update"
    fi
fi

# 5. Clone or update Prompts Repository
if [ "$INSTALL_PROMPTS" = "1" ]; then
    echo "${CYAN}--> Setting up Prompts from bartman081523/prompts...${RESET}"
    if [ "$DRY_RUN" = "0" ]; then
        mkdir -p "$(dirname "${PROMPTS_DIR}")"
        if [ -d "${PROMPTS_DIR}/.git" ]; then
            echo "    Updating existing prompts repository..."
            git -C "${PROMPTS_DIR}" pull --ff-only || true
        else
            LOCAL_ML3="${PROMPTS_LOCAL_PATH:-}"
            if [ -z "${LOCAL_ML3}" ] && [ -d "${HOME}/prompts" ]; then
                LOCAL_ML3="${HOME}/prompts"
            fi
            if [ -n "${LOCAL_ML3}" ] && [ -d "${LOCAL_ML3}" ]; then
                echo "    Linking local prompts from ${LOCAL_ML3}..."
                ln -sfn "${LOCAL_ML3}" "${PROMPTS_DIR}"
            else
                echo "    Cloning ${PROMPTS_REPO_URL}..."
                git clone "${PROMPTS_REPO_URL}" "${PROMPTS_DIR}" || true
            fi
        fi
        echo "    ✓ Prompts available at ${PROMPTS_DIR}"
    fi
fi

# 6. Install Sanitized Skills
if [ "$INSTALL_SKILLS" = "1" ]; then
    echo "${CYAN}--> Installing sanitized agent skills from ${SKILLS_REPO_URL}...${RESET}"
    SKILLS_SRC="${TARGET_REPO_DIR}/skills"
    if [ "$DRY_RUN" = "0" ]; then
        mkdir -p "${SKILLS_DIR}"
        if [ -d "${SKILLS_SRC}" ] && [ -n "$(ls -A "${SKILLS_SRC}" 2>/dev/null)" ]; then
            cp -rn "${SKILLS_SRC}/"* "${SKILLS_DIR}/" 2>/dev/null || true
            echo "    ✓ Sanitized skills installed into ${SKILLS_DIR}"
        else
            if [ -d "${SKILLS_DIR}/.git" ]; then
                git -C "${SKILLS_DIR}" pull --ff-only || true
            else
                git clone "${SKILLS_REPO_URL}" "${SKILLS_DIR}" || true
            fi
            echo "    ✓ Skills cloned into ${SKILLS_DIR}"
        fi

        if [ -d "${HOME}/.claude" ]; then
            mkdir -p "${CLAUDE_SKILLS_DIR}"
            if [ -d "${SKILLS_SRC}" ] && [ -n "$(ls -A "${SKILLS_SRC}" 2>/dev/null)" ]; then
                cp -rn "${SKILLS_SRC}/"* "${CLAUDE_SKILLS_DIR}/" 2>/dev/null || true
            else
                cp -rn "${SKILLS_DIR}/"* "${CLAUDE_SKILLS_DIR}/" 2>/dev/null || true
            fi
            echo "    ✓ Sanitized skills synchronized with ${CLAUDE_SKILLS_DIR}"
        fi
    fi
fi

# 7. Configure Native StatusLine
echo "${CYAN}--> Configuring native StatusLine in settings.json...${RESET}"
if [ "$DRY_RUN" = "0" ]; then
    python3 "${TARGET_REPO_DIR}/agy_modules/infrastruktur/quota_guard.py" --setup-statusline 2>/dev/null || true
    echo "    ✓ Native statusline installed."
fi

# 8. Check PATH configuration
echo ""
case ":${PATH}:" in
    *":${BIN_DIR}:"*)
        PATH_CONFIGURED=1
        ;;
    *)
        PATH_CONFIGURED=0
        ;;
esac

echo "${GREEN}${BOLD}================================================================${RESET}"
echo "${GREEN}${BOLD}✓ Antigravity Full-Stack Mod successfully installed!${RESET}"
echo "${GREEN}${BOLD}================================================================${RESET}"
echo ""
echo "Installed Launchers in ${BOLD}${BIN_DIR}${RESET}:"
echo "  • ${CYAN}agy-full${RESET}        - Full-stack agent (Epistemic + Symbolic + Socratic + SymComp + Quota)"
echo "  • ${CYAN}agy-modular${RESET}     - Configurable stack with modular flags"
echo "  • ${CYAN}agy-epistemic${RESET}   - Rigorous empirical falsification mode"
echo "  • ${CYAN}agy-symbolic${RESET}    - Cognitive construct tracking mode"
echo "  • ${CYAN}agy-socratic${RESET}    - Dialectical three-move inquiry mode"
echo "  • ${CYAN}agy-symcomp${RESET}     - Autonomous construct token compactor"
echo "  • ${CYAN}agy-login${RESET}       - Interactive Multi-Account & Google OAuth Manager"
echo ""

if [ "$PATH_CONFIGURED" = "0" ]; then
    echo "${YELLOW}${BOLD}Notice: ${BIN_DIR} is not currently in your PATH.${RESET}"
    echo "Add it by appending this line to your ~/.bashrc or ~/.zshrc:"
    echo "  ${CYAN}export PATH=\"\$HOME/.local/bin:\$PATH\"${RESET}"
    echo ""
fi

echo "To start coding right away, simply run:"
echo "  ${CYAN}${BOLD}agy-full${RESET}"
echo ""
