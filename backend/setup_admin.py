"""Interactive, local-only setup for the first administrator."""

import sys
import warnings
from getpass import GetPassWarning, getpass

from admin_users import DEFAULT_USERNAME, admin_users_exist, setup_administrator
from database import init_admin_users_table


def main():
    try:
        if not sys.stdin.isatty():
            print('Run setup from an interactive terminal; passwords are never accepted as arguments.', file=sys.stderr)
            return 1
        init_admin_users_table()
        if admin_users_exist():
            print('Accounts already exist. Nothing changed; sign in with your existing credentials.')
            return 0
        print(f'Create the initial administrator: {DEFAULT_USERNAME}')
        print('Choose a unique password of at least 12 characters. Input is hidden.')
        with warnings.catch_warnings():
            warnings.simplefilter('error', GetPassWarning)
            while True:
                password = getpass('New password: ')
                confirmation = getpass('Confirm password: ')
                if len(password) < 12:
                    print('Password must contain at least 12 characters. Try again.')
                elif password != confirmation:
                    print('Passwords do not match. Try again.')
                else:
                    break
        setup_administrator(password)
        print(f'Administrator {DEFAULT_USERNAME} created. Sign in using your chosen password.')
        return 0
    except (EOFError, KeyboardInterrupt):
        print('\nSetup interrupted. Run python backend/setup_admin.py again to resume.', file=sys.stderr)
        return 1
    except GetPassWarning:
        print('Cannot hide password input. Run setup in a terminal that supports hidden input.', file=sys.stderr)
        return 1
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception as error:
        print(
            f'Setup could not complete ({type(error).__name__}). Check the database connection and retry. '
            'If an account was committed, rerunning setup leaves it unchanged.',
            file=sys.stderr,
        )
        return 1


if __name__ == '__main__':
    sys.exit(main())
