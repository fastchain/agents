import os
import sys
from typing import Annotated, Optional, Dict, List, Any
from hcloud import Client, APIException
from hcloud.server_types.domain import ServerType
from hcloud.images.domain import Image
from hcloud.servers.domain import Server
from hcloud.firewalls.domain import Firewall, FirewallResource, FirewallRule
from hcloud.ssh_keys.domain import SSHKey
from hcloud.volumes.domain import Volume
from mcp.server.fastmcp import FastMCP

# 1. Configuration & Client Initialization
HCLOUD_TOKEN = os.getenv("HCLOUD_TOKEN")

if not HCLOUD_TOKEN:
    sys.stderr.write("Error: HCLOUD_TOKEN environment variable is not set.\n")
    sys.exit(1)

# Initialize the Hetzner Client
client = Client(token=HCLOUD_TOKEN)

# Initialize the FastMCP Server
mcp = FastMCP("hetzner-cloud")


def _get_server_by_id_or_name(identifier: str) -> Optional[Server]:
    """Helper to resolve a server object by ID (int) or Name (str)."""
    try:
        # Try treating it as an ID first
        server_id = int(identifier)
        return client.servers.get_by_id(server_id)
    except ValueError:
        # If not an int, treat as a name
        return client.servers.get_by_name(identifier)

def _get_ssh_key_by_id_or_name(identifier: str) -> Optional[SSHKey]:
    """Helper to resolve an SSH Key object by ID (int) or Name (str)."""
    try:
        key_id = int(identifier)
        return client.ssh_keys.get_by_id(key_id)
    except ValueError:
        return client.ssh_keys.get_by_name(identifier)

def _get_firewall_by_id_or_name(identifier: str) -> Optional[Firewall]:
    """Helper to resolve a Firewall object by ID (int) or Name (str)."""
    try:
        fw_id = int(identifier)
        return client.firewalls.get_by_id(fw_id)
    except ValueError:
        return client.firewalls.get_by_name(identifier)

def _get_volume_by_id_or_name(identifier: str) -> Optional[Volume]:
    """Helper to resolve a Volume object by ID (int) or Name (str)."""
    try:
        vol_id = int(identifier)
        return client.volumes.get_by_id(vol_id)
    except ValueError:
        return client.volumes.get_by_name(identifier)


def _format_server(server: Server) -> Dict[str, Any]:
    """Helper to format a Server object into a clean dictionary."""
    ipv4 = server.public_net.ipv4.ip if server.public_net.ipv4 else "N/A"
    return {
        "id": server.id,
        "name": server.name,
        "status": server.status,
        "server_type": server.server_type.name,
        "public_ip": ipv4,
        "location": server.location.name,
        "city": server.location.city,
    }


# 2. Tool Implementations

@mcp.tool()
def list_servers() -> Any:
    """
    List all available servers in the Hetzner Cloud project.
    Returns a list of servers with their ID, name, status, IP, and type.
    """
    try:
        servers = client.servers.get_all()
        return [_format_server(s) for s in servers]
    except APIException as e:
        return {"error": f"Hetzner API Error: {str(e)}"}
    except Exception as e:
        return {"error": f"Unexpected error: {str(e)}"}


@mcp.tool()
def get_server_details(
        name_or_id: Annotated[str, "The unique ID or Name of the server"]
) -> Dict[str, Any]:
    """
    Get detailed information about a specific server.
    """
    try:
        server = _get_server_by_id_or_name(name_or_id)
        if not server:
            return {"error": f"Server '{name_or_id}' not found."}

        # Build a detailed response
        details = _format_server(server)
        details.update({
            "cores": server.server_type.cores,
            "memory_gb": server.server_type.memory,
            "disk_gb": server.server_type.disk,
            "image": server.image.name if server.image else "Unknown",
            "created": server.created.isoformat(),
            "locked": server.locked,
        })
        return details
    except APIException as e:
        return {"error": f"Hetzner API Error: {str(e)}"}


@mcp.tool()
def start_server(
        name_or_id: Annotated[str, "The unique ID or Name of the server"]
) -> str:
    """
    Power on a stopped server.
    """
    try:
        server = _get_server_by_id_or_name(name_or_id)
        if not server:
            return f"Error: Server '{name_or_id}' not found."

        if server.status == "running":
            return f"Server '{server.name}' is already running."

        action = server.power_on()
        return f"Power on command sent to '{server.name}'. Action ID: {action.id}"
    except APIException as e:
        return f"Hetzner API Error: {str(e)}"


@mcp.tool()
def stop_server(
        name_or_id: Annotated[str, "The unique ID or Name of the server"]
) -> str:
    """
    Shut down a server gracefully (ACPI shutdown).
    """
    try:
        server = _get_server_by_id_or_name(name_or_id)
        if not server:
            return f"Error: Server '{name_or_id}' not found."

        if server.status == "off":
            return f"Server '{server.name}' is already off."

        action = server.shutdown()
        return f"Shutdown command sent to '{server.name}'. Action ID: {action.id}"
    except APIException as e:
        return f"Hetzner API Error: {str(e)}"


@mcp.tool()
def reboot_server(
        name_or_id: Annotated[str, "The unique ID or Name of the server"]
) -> str:
    """
    Reboot a server. Tries a soft reboot first.
    """
    try:
        server = _get_server_by_id_or_name(name_or_id)
        if not server:
            return f"Error: Server '{name_or_id}' not found."

        action = server.reboot()
        return f"Reboot command sent to '{server.name}'. Action ID: {action.id}"
    except APIException as e:
        return f"Hetzner API Error: {str(e)}"


@mcp.tool()
def create_server(
        name: Annotated[str, "The name of the new server"],
        server_type: Annotated[str, "The server type (e.g., cx11, cpx11, cpx21)"] = "cx11",
        image: Annotated[str, "The OS image (e.g., ubuntu-24.04, debian-12)"] = "ubuntu-24.04",
        ssh_keys: Annotated[List[str], "List of SSH key names or IDs to inject"] = None
) -> Dict[str, Any]:
    """
    Create a new cloud server.
    """
    try:
        # Resolve Types
        type_obj = client.server_types.get_by_name(server_type)
        if not type_obj:
            return {"error": f"Server type '{server_type}' is invalid."}

        image_obj = client.images.get_by_name_and_architecture(image, type_obj.architecture)
        if not image_obj:
            return {"error": f"Image '{image}' not found for architecture {type_obj.architecture}."}

        # Resolve SSH Keys
        ssh_key_objs = []
        if ssh_keys:
            for key_ident in ssh_keys:
                k = _get_ssh_key_by_id_or_name(key_ident)
                if k:
                    ssh_key_objs.append(k)
                else:
                    return {"error": f"SSH Key '{key_ident}' not found."}

        # Create
        response = client.servers.create(
            name=name,
            server_type=type_obj,
            image=image_obj,
            ssh_keys=ssh_key_objs
        )

        server = response.server
        root_pass = response.root_password

        return {
            "status": "success",
            "message": f"Server '{server.name}' created successfully.",
            "id": server.id,
            "public_ip": server.public_net.ipv4.ip,
            "root_password": root_pass if root_pass else "SSH Key used (no password returned)"
        }

    except APIException as e:
        return {"error": f"Hetzner API Error: {str(e)}"}
    except Exception as e:
        return {"error": f"Unexpected error: {str(e)}"}

# --- SSH Key Management ---

@mcp.tool()
def list_ssh_keys() -> Any:
    """List all SSH keys."""
    try:
        keys = client.ssh_keys.get_all()
        return [{"id": k.id, "name": k.name, "fingerprint": k.fingerprint} for k in keys]
    except APIException as e:
        return {"error": f"Hetzner API Error: {str(e)}"}

@mcp.tool()
def create_ssh_key(name: str, public_key: str) -> Dict[str, Any]:
    """Create a new SSH key."""
    try:
        key = client.ssh_keys.create(name=name, public_key=public_key)
        return {"id": key.id, "name": key.name, "fingerprint": key.fingerprint}
    except APIException as e:
        return {"error": f"Hetzner API Error: {str(e)}"}

@mcp.tool()
def delete_ssh_key(name_or_id: str) -> str:
    """Delete an SSH key."""
    try:
        key = _get_ssh_key_by_id_or_name(name_or_id)
        if not key:
            return f"Error: SSH Key '{name_or_id}' not found."
        client.ssh_keys.delete(key)
        return f"SSH Key '{key.name}' deleted."
    except APIException as e:
        return f"Hetzner API Error: {str(e)}"

# --- Firewall Management ---

@mcp.tool()
def list_firewalls() -> Any:
    """List all firewalls."""
    try:
        firewalls = client.firewalls.get_all()
        return [{"id": f.id, "name": f.name, "rules_count": len(f.rules)} for f in firewalls]
    except APIException as e:
        return {"error": f"Hetzner API Error: {str(e)}"}

@mcp.tool()
def create_firewall(name: str) -> Dict[str, Any]:
    """Create a new firewall (empty)."""
    try:
        response = client.firewalls.create(name=name)
        fw = response.firewall
        return {"id": fw.id, "name": fw.name}
    except APIException as e:
        return {"error": f"Hetzner API Error: {str(e)}"}

@mcp.tool()
def delete_firewall(name_or_id: str) -> str:
    """Delete a firewall."""
    try:
        fw = _get_firewall_by_id_or_name(name_or_id)
        if not fw:
            return f"Error: Firewall '{name_or_id}' not found."
        client.firewalls.delete(fw)
        return f"Firewall '{fw.name}' deleted."
    except APIException as e:
        return f"Hetzner API Error: {str(e)}"

@mcp.tool()
def apply_firewall_to_server(
    firewall_name_or_id: str,
    server_name_or_id: str
) -> str:
    """Apply a firewall to a server."""
    try:
        fw = _get_firewall_by_id_or_name(firewall_name_or_id)
        if not fw:
            return f"Error: Firewall '{firewall_name_or_id}' not found."
        
        server = _get_server_by_id_or_name(server_name_or_id)
        if not server:
            return f"Error: Server '{server_name_or_id}' not found."
            
        # Create a FirewallResource for the server
        resource = FirewallResource(type=FirewallResource.TYPE_SERVER, server=server)
        
        action = fw.apply_to_resources([resource])
        return f"Firewall '{fw.name}' applied to server '{server.name}'."
    except APIException as e:
        return f"Hetzner API Error: {str(e)}"

@mcp.tool()
def remove_firewall_from_server(
    firewall_name_or_id: str,
    server_name_or_id: str
) -> str:
    """Remove a firewall from a server."""
    try:
        fw = _get_firewall_by_id_or_name(firewall_name_or_id)
        if not fw:
            return f"Error: Firewall '{firewall_name_or_id}' not found."
        
        server = _get_server_by_id_or_name(server_name_or_id)
        if not server:
            return f"Error: Server '{server_name_or_id}' not found."
            
        resource = FirewallResource(type=FirewallResource.TYPE_SERVER, server=server)
        
        action = fw.remove_from_resources([resource])
        return f"Firewall '{fw.name}' removed from server '{server.name}'."
    except APIException as e:
        return f"Hetzner API Error: {str(e)}"

@mcp.tool()
def add_firewall_rule(
    firewall_name_or_id: str,
    direction: Annotated[str, "in or out"],
    protocol: Annotated[str, "tcp, udp, icmp, esp, gre"],
    port: Optional[str] = None,
    source_ips: Optional[List[str]] = None,
    destination_ips: Optional[List[str]] = None
) -> str:
    """
    Add a rule to a firewall.
    Note: This appends to existing rules.
    """
    try:
        fw = _get_firewall_by_id_or_name(firewall_name_or_id)
        if not fw:
            return f"Error: Firewall '{firewall_name_or_id}' not found."

        # Construct the new rule
        new_rule = FirewallRule(
            direction=direction,
            protocol=protocol,
            port=port,
            source_ips=source_ips,
            destination_ips=destination_ips
        )
        
        # Get existing rules and append
        rules = fw.rules
        rules.append(new_rule)
        
        action = fw.set_rules(rules)
        return f"Rule added to firewall '{fw.name}'."
    except APIException as e:
        return f"Hetzner API Error: {str(e)}"

# --- Volume (Storage) Management ---

@mcp.tool()
def list_volumes() -> Any:
    """List all volumes (Block Storage)."""
    try:
        volumes = client.volumes.get_all()
        return [{
            "id": v.id, 
            "name": v.name, 
            "size": v.size, 
            "location": v.location.name, 
            "server": v.server.name if v.server else None
        } for v in volumes]
    except APIException as e:
        return {"error": f"Hetzner API Error: {str(e)}"}

@mcp.tool()
def create_volume(name: str, size: int, location: str = "nbg1") -> Dict[str, Any]:
    """Create a new volume. Size in GB."""
    try:
        loc = client.locations.get_by_name(location)
        if not loc:
             return {"error": f"Location '{location}' not found."}
        response = client.volumes.create(name=name, size=size, location=loc)
        vol = response.volume
        return {"id": vol.id, "name": vol.name, "size": vol.size}
    except APIException as e:
        return {"error": f"Hetzner API Error: {str(e)}"}

@mcp.tool()
def delete_volume(name_or_id: str) -> str:
    """Delete a volume."""
    try:
        vol = _get_volume_by_id_or_name(name_or_id)
        if not vol:
            return f"Error: Volume '{name_or_id}' not found."
        client.volumes.delete(vol)
        return f"Volume '{vol.name}' deleted."
    except APIException as e:
        return f"Hetzner API Error: {str(e)}"

@mcp.tool()
def attach_volume(
    volume_name_or_id: str,
    server_name_or_id: str,
    automount: bool = False
) -> str:
    """Attach a volume to a server."""
    try:
        vol = _get_volume_by_id_or_name(volume_name_or_id)
        if not vol:
            return f"Error: Volume '{volume_name_or_id}' not found."
            
        server = _get_server_by_id_or_name(server_name_or_id)
        if not server:
            return f"Error: Server '{server_name_or_id}' not found."
            
        action = vol.attach(server, automount=automount)
        return f"Volume '{vol.name}' attached to server '{server.name}'."
    except APIException as e:
        return f"Hetzner API Error: {str(e)}"

@mcp.tool()
def detach_volume(volume_name_or_id: str) -> str:
    """Detach a volume from its server."""
    try:
        vol = _get_volume_by_id_or_name(volume_name_or_id)
        if not vol:
            return f"Error: Volume '{volume_name_or_id}' not found."
            
        action = vol.detach()
        return f"Volume '{vol.name}' detached."
    except APIException as e:
        return f"Hetzner API Error: {str(e)}"

if __name__ == "__main__":
    # HOST must be 0.0.0.0 to work inside Docker
    mcp.run(transport='http', host='0.0.0.0', port=8000)
