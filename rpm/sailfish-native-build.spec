Name:           sailfish-native-build
Version:        0.1.0
Release:        4
Summary:        Native Sailfish OS RPM build command
License:        MIT
Group:          Development/Tools
BuildArch:      noarch
Source0:        %{name}-%{version}.tar.gz
BuildRequires:  python3-base >= 3.7
BuildRequires:  git
BuildRequires:  rpm-python
Requires:       python3-base >= 3.7
Requires:       rpm-python
Requires:       bash
Requires:       gcc
Requires:       gcc-c++
Requires:       binutils
Requires:       make
Requires:       pkgconfig
Requires:       rpm-build
Requires:       meego-rpm-config
Requires:       zypper
Requires:       /usr/bin/devel-su
Requires:       git
Requires:       tar
Requires:       gzip
Requires:       bzip2
Requires:       xz
Requires:       patch
Requires:       diffutils
Requires:       coreutils
Requires:       findutils
Requires:       grep
Requires:       sed
Requires:       file
Requires:       cpio
Requires:       rpmlint

%description
mbn builds RPM packages directly on a Sailfish OS host using the
installed compiler, RPM macros and repositories. It resolves spec build
dependencies, builds in the source checkout and retains build logs and
provenance. It does not use SDK targets, Scratchbox2 or emulation.

%prep
%setup -q

%build
# No compilation is needed.

%install
install -D -m 0755 mbn %{buildroot}%{_bindir}/mbn

%check
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v

%files
%license LICENSE
%doc README.md
%{_bindir}/mbn

%changelog
* Thu Oct 15 2026 Andrew Branson <andrew.branson@jolla.com> - 0.1.0-4
- Check versioned build requirements using RPM dependency resolution.

* Wed Oct 14 2026 Andrew Branson <andrew.branson@jolla.com> - 0.1.0-3
- Add native in-place RPM builds with dependency installation and validation.
- Stream build output and export RPMs directly to the project output directory.
