#!/bin/bash
#
# Local deployment script for BotTrader services (runs on server)
# Usage: ./scripts/deploy-local.sh [service_name] [--verify-only]
#
# Examples:
#   ./scripts/deploy-local.sh webhook          # Deploy webhook service
#   ./scripts/deploy-local.sh sighook          # Deploy sighook service
#   ./scripts/deploy-local.sh report           # Deploy report service
#   ./scripts/deploy-local.sh all              # Deploy all services
#   ./scripts/deploy-local.sh webhook --verify-only  # Only verify current version
#

set -e  # Exit on error

# Configuration
COMPOSE_FILE="docker-compose.aws.yml"
REPO_PATH="/opt/bot"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Service definitions (list format for compatibility with older bash)
VALID_SERVICES="webhook sighook report"

is_valid_service() {
    local service=$1
    echo "$VALID_SERVICES" | grep -q "\b$service\b"
}

# Function to print colored output
print_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

print_header() {
    echo ""
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}$(printf '=%.0s' {1..80})${NC}"
}

# Function to get local git info
get_local_version() {
    local commit=$(git rev-parse --short=8 HEAD)
    local branch=$(git rev-parse --abbrev-ref HEAD)
    echo "${branch}@${commit}"
}

# Function to verify deployed version
verify_deployed_version() {
    local service=$1
    print_info "Checking deployed version of $service..."

    # Use full logs and search for banner (more reliable than --tail with busy services)
    docker logs $service 2>&1 | grep -A 10 "🚀.*SERVICE STARTING" | tail -12 || {
        print_warning "Could not find version info in logs (service may not have started yet)"
        return 1
    }
}

# Function to deploy a single service
deploy_service() {
    local service=$1

    print_header "DEPLOYING $service LOCALLY"

    # Step 1: Pull latest code from repository
    print_info "Step 1/4: Pulling latest code from repository..."
    cd $REPO_PATH
    git pull || {
        print_error "Failed to pull code"
        return 1
    }
    print_success "Code pulled successfully"

    # Step 2: Prune build cache
    print_info "Step 2/4: Pruning Docker build cache..."
    docker builder prune -f > /dev/null 2>&1 || {
        print_warning "Build cache prune failed (not critical)"
    }
    print_success "Build cache pruned"

    # Step 3: Build and deploy
    print_info "Step 3/4: Building and deploying $service..."
    docker compose -f $COMPOSE_FILE build $service && \
        docker compose -f $COMPOSE_FILE up -d $service || {
        print_error "Failed to build/deploy $service"
        return 1
    }
    print_success "$service deployed successfully"

    # Step 4: Wait for service to start and verify version
    print_info "Step 4/4: Waiting for $service to start..."
    sleep 5

    print_info "Verifying deployed version..."
    verify_deployed_version "$service"

    print_success "Deployment of $service complete!"
    echo ""
}

# Function to verify all services
verify_all_services() {
    print_header "VERIFYING ALL SERVICE VERSIONS"

    for service in $VALID_SERVICES; do
        echo ""
        print_info "Checking $service..."
        verify_deployed_version "$service"
    done

    echo ""
}

# Main script logic
main() {
    local service_name=$1
    local verify_only=$2

    # Check if we're in the correct directory
    if [[ ! -f "$COMPOSE_FILE" ]]; then
        print_error "docker-compose.aws.yml not found!"
        print_info "Please run this script from /opt/bot or set REPO_PATH"
        exit 1
    fi

    # Show current local version
    local local_version=$(get_local_version)
    print_info "Local version: $local_version"
    echo ""

    # Handle verify-only mode
    if [[ "$verify_only" == "--verify-only" ]] || [[ "$service_name" == "--verify-only" ]]; then
        verify_all_services
        exit 0
    fi

    # Validate service name
    if [[ -z "$service_name" ]]; then
        print_error "Usage: $0 [service_name] [--verify-only]"
        print_info "Available services: $VALID_SERVICES all"
        print_info "Use --verify-only to check current deployed versions"
        exit 1
    fi

    # Deploy service(s)
    if [[ "$service_name" == "all" ]]; then
        for service in $VALID_SERVICES; do
            deploy_service "$service"
        done
    elif is_valid_service "$service_name"; then
        deploy_service "$service_name"
    else
        print_error "Unknown service: $service_name"
        print_info "Available services: $VALID_SERVICES all"
        exit 1
    fi

    print_success "All deployments completed successfully!"
}

# Run main function
main "$@"
