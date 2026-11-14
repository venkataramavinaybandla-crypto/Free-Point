data "aws_ssm_parameter" "al2023_ami" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "aws_instance" "nat" {
  ami                         = data.aws_ssm_parameter.al2023_ami.value
  instance_type               = "t3.nano"
  subnet_id                   = aws_subnet.public[0].id
  vpc_security_group_ids      = [aws_security_group.nat.id]
  source_dest_check           = false
  associate_public_ip_address = true

  user_data = <<-EOF
    #!/bin/bash
    set -xe
    echo "net.ipv4.ip_forward = 1" > /etc/sysctl.d/99-ipforward.conf
    sysctl -p /etc/sysctl.d/99-ipforward.conf

    PRIMARY_IF=$(ip -o -4 route show to default | awk '{print $5}')
    iptables -t nat -A POSTROUTING -o "$PRIMARY_IF" -j MASQUERADE

    cat <<'UNIT' > /etc/systemd/system/nat-masquerade.service
    [Unit]
    Description=NAT IP Forwarding and Masquerading
    After=network.target

    [Service]
    Type=oneshot
    ExecStart=/bin/sh -c 'PRIMARY_IF=$(ip -o -4 route show to default | awk "{print \$5}"); iptables -t nat -C POSTROUTING -o "$PRIMARY_IF" -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -o "$PRIMARY_IF" -j MASQUERADE'
    RemainAfterExit=yes

    [Install]
    WantedBy=multi-user.target
    UNIT

    systemctl daemon-reload
    systemctl enable nat-masquerade.service
  EOF

  tags = {
    Name = "free-point-nat-instance"
  }
}
