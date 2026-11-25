#!/usr/bin/env bash
# Sourced by the installer after successful setup. Keep Bash 3 compatibility.

vlad_add_profile_path() {
    local profile="$1" shell_style="$2"
    local marker='# >>> VLAD user PATH >>>'
    if [[ -f "$profile" ]] && grep -Fxq "$marker" "$profile"; then
        return 0
    fi
    mkdir -p "$(dirname "$profile")" || return 1
    {
        printf '\n%s\n' "$marker" &&
        if [[ "$shell_style" == fish ]]; then
            printf '%s\n' 'contains -- "$HOME/.local/bin" $PATH; or set -gx PATH $PATH "$HOME/.local/bin"'
        else
            printf '%s\n' 'case ":${PATH:-}:" in' \
                '    *":$HOME/.local/bin:"*) ;;' \
                '    *) export PATH="${PATH:+$PATH:}$HOME/.local/bin" ;;' \
                'esac'
        fi &&
        printf '%s\n' '# <<< VLAD user PATH <<<'
    } >>"$profile"
}

vlad_install_user_launcher() {
    local launcher="$1" temporary
    local marker='# Managed by VLAD install.sh'
    if [[ -L "$launcher" || -e "$launcher" ]] &&
        { [[ -L "$launcher" || ! -f "$launcher" ]] || ! grep -Fxq "$marker" "$launcher"; }; then
        printf 'VLAD: preserving the existing command at %s.\n' "$launcher" >&2
        return 1
    fi
    mkdir -p "${launcher%/*}" || return 1
    temporary=$(mktemp "${launcher%/*}/.vlad.XXXXXX") || return 1
    if ! {
        printf '#!/usr/bin/env bash\n%s\nexec %q "$@"\n' "$marker" "${VLAD_ROOT}/vlad" >"$temporary" &&
        chmod 755 "$temporary" &&
        mv -f "$temporary" "$launcher"
    }; then
        rm -f "$temporary"
        return 1
    fi
}

vlad_add_to_path() {
    local bin_dir="${HOME}/.local/bin" launcher shell_name login_file zsh_dir
    launcher="$bin_dir/vlad"
    vlad_install_user_launcher "$launcher" || return 1
    shell_name="${SHELL:-bash}"
    case "${shell_name##*/}" in
        bash)
            login_file="$HOME/.profile"
            if [[ -f "$HOME/.bash_profile" ]]; then login_file="$HOME/.bash_profile"
            elif [[ -f "$HOME/.bash_login" ]]; then login_file="$HOME/.bash_login"; fi
            vlad_add_profile_path "$login_file" sh && vlad_add_profile_path "$HOME/.bashrc" sh || return 1
            ;;
        zsh)
            zsh_dir="${ZDOTDIR:-$HOME}"
            vlad_add_profile_path "$zsh_dir/.zprofile" sh && vlad_add_profile_path "$zsh_dir/.zshrc" sh || return 1
            ;;
        fish)
            vlad_add_profile_path "${XDG_CONFIG_HOME:-$HOME/.config}/fish/conf.d/vlad-path.fish" fish || return 1
            ;;
        sh|dash|ash|ksh)
            vlad_add_profile_path "$HOME/.profile" sh || return 1
            ;;
        *)
            say "Launcher installed at $launcher. Add $bin_dir to PATH in your shell's configuration."
            return 0
            ;;
    esac
    if command -v vlad >/dev/null 2>&1 && [[ "$(command -v vlad)" != "$launcher" ]]; then
        say "Your existing 'vlad' command takes precedence. This installation is at $launcher."
    fi
    say "Launcher installed at $launcher. Open a new terminal to use 'vlad' from any directory."
}
