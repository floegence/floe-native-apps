# Disposable prototype compiler only; not a production component recipe.
FROM public.ecr.aws/ubuntu/ubuntu@sha256:22a8228e1e48cbe7e0e0f2056e752ffb8a35950cda150a4e5e16417200bec648
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    gcc g++ pkg-config libwayland-dev libxkbcommon-dev \
    libpixman-1-dev libgtk-4-dev wayland-protocols qt6-base-private-dev \
    qt6-wayland python3-pyqt6 python3-gi python3-pil python3-xlib \
    dbus-daemon xauth make gettext libtool iso-codes patch libdbus-1-dev \
    libxfixes-dev gir1.2-ibus-1.0 ibus ibus-gtk4 && rm -rf /var/lib/apt/lists/*
